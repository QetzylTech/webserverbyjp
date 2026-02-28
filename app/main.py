"""Web dashboard for controlling and monitoring a Minecraft systemd service.

This app provides:
- Service controls (start/stop/manual backup)
- Live server and Minecraft stats
- Systemd log viewer
- Automatic idle shutdown and session-based backup scheduling"""
from flask import Flask
from pathlib import Path
import threading
import re
import json
from collections import deque
from zoneinfo import ZoneInfo
from app.core.config import apply_default_flask_config, resolve_secret_key
from app.core.device_map import get_device_name_map as _device_name_map_lookup
from app.core.filesystem_utils import (
    list_download_files as _list_download_files,
    read_recent_file_lines as _read_recent_file_lines,
    safe_file_mtime_ns as _safe_file_mtime_ns,
    safe_filename_in_dir as _safe_filename_in_dir,
)
from app.core.logging_setup import build_loggers
from app.core.web_config import WebConfig
from app.services import control_plane as control_plane_service
from app.services import bootstrap as bootstrap_service
from app.services import app_lifecycle as app_lifecycle_service
from app.services import dashboard_runtime as dashboard_runtime_service
from app.services import debug_bindings as debug_bindings_service
from app.services import debug_tools as debug_tools_service
from app.services import minecraft_runtime as minecraft_runtime_service
from app.services import request_bindings as request_bindings_service
from app.services import runtime_bindings as runtime_bindings_service
from app.services import session_store as session_store_service
from app.services import session_watchers as session_watchers_service
from app.services import state_builder as state_builder_service
from app.services import status_cache as status_cache_service
from app.services import system_bindings as system_bindings_service
from app.services.system_metrics import (
    get_cpu_frequency,
    get_cpu_usage_per_core,
    get_ram_usage,
    get_storage_usage,
)
from app.services import world_bindings as world_bindings_service
from app.routes.dashboard_routes import register_routes
from app.state import BackupState, SessionState

APP_DIR = Path(__file__).resolve().parent.parent
app = Flask(
    __name__,
    template_folder=str(APP_DIR / "templates"),
    static_folder=str(APP_DIR / "static"),
)
WEB_CONF_PATH = APP_DIR / "mcweb.env"
_WEB_CFG = WebConfig(WEB_CONF_PATH, APP_DIR)
_WEB_CFG_VALUES = dict(_WEB_CFG.values)
_cfg_str = _WEB_CFG.get_str
_cfg_int = _WEB_CFG.get_int
_cfg_float = _WEB_CFG.get_float
_cfg_path = _WEB_CFG.get_path
STATE = None

app.config["SECRET_KEY"] = resolve_secret_key(_cfg_str, "MCWEB_SECRET_KEY", "FLASK_SECRET_KEY")
apply_default_flask_config(app)

# Core service and application settings.
FAVICON_URL = "https://static.wikia.nocookie.net/logopedia/images/e/e3/Minecraft_Launcher.svg/revision/latest/scale-to-width-down/250?cb=20230616222246"
SERVICE = _cfg_str("SERVICE", "minecraft")
ADMIN_PASSWORD_HASH = _cfg_str("MCWEB_ADMIN_PASSWORD_HASH", "")
BACKUP_SCRIPT = _cfg_path("BACKUP_SCRIPT", APP_DIR / "scripts" / "backup.sh")
BACKUP_DIR = _cfg_path("BACKUP_DIR", Path("/home/marites/backups"))
WORLD_DIR = Path("/opt/Minecraft/config")
CRASH_REPORTS_DIR = _cfg_path("CRASH_REPORTS_DIR", APP_DIR.parent / "crash-reports")
MINECRAFT_LOGS_DIR = _cfg_path("MINECRAFT_LOGS_DIR", APP_DIR.parent / "logs")
MCWEB_LOG_DIR = _cfg_path("MCWEB_LOG_DIR", APP_DIR / "logs")
BACKUP_LOG_FILE = MCWEB_LOG_DIR / "backup.log"
MCWEB_ACTION_LOG_FILE = MCWEB_LOG_DIR / "mcweb_actions.log"
MCWEB_LOG_FILE = MCWEB_LOG_DIR / "mcweb.log"
DEBUG_PAGE_LOG_FILE = MCWEB_LOG_DIR / "debug_page.log"
DATA_DIR = _cfg_path("DATA_DIR", APP_DIR / "data")
DOCS_DIR = _cfg_path("DOCS_DIR", APP_DIR / "doc")
BACKUP_STATE_FILE = _cfg_path("BACKUP_STATE_FILE", DATA_DIR / "state.txt")
USERS_FILE = _cfg_path("USERS_FILE", DATA_DIR / "users.txt")
SESSION_FILE = DATA_DIR / "session.txt"
DOC_README_URL = _cfg_str("DOC_README_URL", "/doc/server_setup_doc.md")
DEVICE_MAP_CSV_PATH = _cfg_path(
    "DEVICE_MAP_CSV_PATH",
    DATA_DIR / "marites.minecraft@gmail.com-devices-2026-02-26T04-37-44-487Z.csv",
)
DEVICE_FALLMAP_PATH = _cfg_path("DEVICE_FALLMAP_PATH", DATA_DIR / "fallmap.txt")
# "PST" here refers to Philippines Standard Time (UTC+8), not Pacific Time.
try:
    DISPLAY_TZ = ZoneInfo(_cfg_str("DISPLAY_TZ", "Asia/Manila"))
except Exception:
    DISPLAY_TZ = ZoneInfo("Asia/Manila")
log_mcweb_action, log_mcweb_log, log_mcweb_exception, log_debug_page_action = build_loggers(
    DISPLAY_TZ,
    MCWEB_LOG_DIR,
    MCWEB_ACTION_LOG_FILE,
    MCWEB_LOG_FILE,
    DEBUG_PAGE_LOG_FILE,
)
APP_PROFILE = _cfg_str("MCWEB_PROFILE", "core").strip().lower()
DEBUG_PROFILE_ENABLED = APP_PROFILE == "debug"
RAW_DEBUG_ENABLED = _cfg_str("DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
DEBUG_ENABLED = RAW_DEBUG_ENABLED and DEBUG_PROFILE_ENABLED
DEV_ENABLED = _cfg_str("DEV", "false").strip().lower() in {"1", "true", "yes", "on"}
DEBUG_PAGE_VISIBLE = DEBUG_ENABLED or DEV_ENABLED
MAINTENANCE_SCOPE_BACKUP_ZIP = _cfg_str("MAINTENANCE_SCOPE_BACKUP_ZIP", "true").strip().lower() in {"1", "true", "yes", "on"}
MAINTENANCE_SCOPE_STALE_WORLD_DIR = _cfg_str("MAINTENANCE_SCOPE_STALE_WORLD_DIR", "true").strip().lower() in {"1", "true", "yes", "on"}
MAINTENANCE_SCOPE_OLD_WORLD_ZIP = _cfg_str("MAINTENANCE_SCOPE_OLD_WORLD_ZIP", "true").strip().lower() in {"1", "true", "yes", "on"}
# Hard safety guards are intentionally fixed and not env-configurable.
MAINTENANCE_GUARD_NEVER_DELETE_NEWEST_N = 1
MAINTENANCE_GUARD_NEVER_DELETE_LAST_BACKUP = True
MAINTENANCE_GUARD_PROTECT_ACTIVE_WORLD = True
RCON_HOST = _cfg_str("RCON_HOST", "127.0.0.1")
RCON_PORT = _cfg_int("RCON_PORT", 25575, minimum=1)
SERVER_PROPERTIES_CANDIDATES = [
    Path("/opt/Minecraft/server.properties"),
    Path("/opt/Minecraft/server/server.properties"),
    APP_DIR / "server.properties",
    APP_DIR.parent / "server.properties",
]
DEBUG_WORLD_NAME = "debug_world"
DEBUG_MOTD = "debugging in progress"
DEBUG_SERVER_PROPERTIES_KEYS = (
    "accepts-transfers",
    "allow-flight",
    "allow-nether",
    "broadcast-console-to-ops",
    "broadcast-rcon-to-ops",
    "bug-report-link",
    "difficulty",
    "enable-command-block",
    "enable-jmx-monitoring",
    "enable-query",
    "enable-rcon",
    "enable-status",
    "enforce-secure-profile",
    "enforce-whitelist",
    "entity-broadcast-range-percentage",
    "force-gamemode",
    "function-permission-level",
    "gamemode",
    "generate-structures",
    "generator-settings",
    "hardcore",
    "hide-online-players",
    "initial-disabled-packs",
    "initial-enabled-packs",
    "level-name",
    "level-seed",
    "level-type",
    "log-ips",
    "max-build-height",
    "max-chained-neighbor-updates",
    "max-players",
    "max-tick-time",
    "max-world-size",
    "motd",
    "network-compression-threshold",
    "online-mode",
    "op-permission-level",
    "pause-when-empty-seconds",
    "player-idle-timeout",
    "prevent-proxy-connections",
    "previews-chat",
    "pvp",
    "query.port",
    "rate-limit",
    "rcon.password",
    "rcon.port",
    "region-file-compression",
    "require-resource-pack",
    "resource-pack",
    "resource-pack-id",
    "resource-pack-prompt",
    "resource-pack-sha1",
    "server-ip",
    "server-port",
    "simulation-distance",
    "snooper-enabled",
    "spawn-animals",
    "spawn-monsters",
    "spawn-npcs",
    "spawn-protection",
    "sync-chunk-writes",
    "text-filtering-config",
    "text-filtering-version",
    "use-native-transport",
    "view-distance",
    "white-list",
)
DEBUG_SERVER_PROPERTIES_FORCED_VALUES = {
    "level-name": DEBUG_WORLD_NAME,
}
DEBUG_SERVER_PROPERTIES_ENUMS = {
    "difficulty": ("peaceful", "easy", "normal", "hard"),
    "gamemode": ("survival", "creative", "adventure", "spectator"),
    "level-type": ("default", "flat", "large_biomes", "amplified", "single_biome_surface"),
    "region-file-compression": ("deflate", "lz4", "none"),
}
DEBUG_SERVER_PROPERTIES_INT_KEYS = {
    "entity-broadcast-range-percentage",
    "function-permission-level",
    "max-build-height",
    "max-chained-neighbor-updates",
    "max-players",
    "max-tick-time",
    "max-world-size",
    "network-compression-threshold",
    "op-permission-level",
    "pause-when-empty-seconds",
    "player-idle-timeout",
    "query.port",
    "rate-limit",
    "rcon.port",
    "server-port",
    "simulation-distance",
    "spawn-protection",
    "text-filtering-version",
    "view-distance",
}
DEBUG_SERVER_PROPERTIES_BOOL_KEYS = {
    "accepts-transfers",
    "allow-flight",
    "allow-nether",
    "broadcast-console-to-ops",
    "broadcast-rcon-to-ops",
    "enable-command-block",
    "enable-jmx-monitoring",
    "enable-query",
    "enable-rcon",
    "enable-status",
    "enforce-secure-profile",
    "enforce-whitelist",
    "force-gamemode",
    "generate-structures",
    "hardcore",
    "hide-online-players",
    "log-ips",
    "online-mode",
    "prevent-proxy-connections",
    "previews-chat",
    "pvp",
    "require-resource-pack",
    "snooper-enabled",
    "spawn-animals",
    "spawn-monsters",
    "spawn-npcs",
    "sync-chunk-writes",
    "use-native-transport",
    "white-list",
}

# Backup and automation timing controls.
BACKUP_INTERVAL_HOURS = _cfg_float("BACKUP_INTERVAL_HOURS", 3.0, minimum=1/60.0)
BACKUP_INTERVAL_SECONDS = max(60, int(BACKUP_INTERVAL_HOURS * 3600))
IDLE_ZERO_PLAYERS_SECONDS = _cfg_int("IDLE_ZERO_PLAYERS_SECONDS", 180, minimum=10)
IDLE_CHECK_INTERVAL_SECONDS = _cfg_int("IDLE_CHECK_INTERVAL_SECONDS", 5, minimum=1)
IDLE_CHECK_INTERVAL_ACTIVE_SECONDS = _cfg_int("IDLE_CHECK_INTERVAL_ACTIVE_SECONDS", IDLE_CHECK_INTERVAL_SECONDS, minimum=1)
IDLE_CHECK_INTERVAL_OFF_SECONDS = _cfg_int("IDLE_CHECK_INTERVAL_OFF_SECONDS", max(IDLE_CHECK_INTERVAL_ACTIVE_SECONDS, 15), minimum=1)

# Shared watcher state (protected by the locks below).
idle_zero_players_since = None
idle_lock = threading.Lock()
backup_state = BackupState(
    lock=threading.Lock(),
    run_lock=threading.Lock(),
    periodic_runs=0,
    last_error="",
)
session_state = SessionState(
    session_file=SESSION_FILE,
    initialized=False,
    init_lock=threading.Lock(),
)
service_status_intent = None
service_status_intent_lock = threading.Lock()
restore_lock = threading.Lock()
users_file_lock = threading.Lock()
debug_env_lock = threading.Lock()
debug_env_original_values = dict(_WEB_CFG_VALUES)
debug_env_overrides = {}

OFF_STATES = {"inactive", "failed"}
LOG_SOURCE_KEYS = ("minecraft", "backup", "mcweb", "mcweb_log")

# Cache Minecraft runtime probes so rapid UI polling does not overwhelm RCON.
MC_QUERY_INTERVAL_SECONDS = _cfg_int("MC_QUERY_INTERVAL_SECONDS", 3, minimum=1)
RCON_STARTUP_FALLBACK_AFTER_SECONDS = _cfg_int("RCON_STARTUP_FALLBACK_AFTER_SECONDS", 120, minimum=1)
RCON_STARTUP_FALLBACK_INTERVAL_SECONDS = _cfg_int("RCON_STARTUP_FALLBACK_INTERVAL_SECONDS", 5, minimum=1)
mc_query_lock = threading.Lock()
mc_last_query_at = 0.0
mc_cached_players_online = "unknown"
mc_cached_tick_rate = "unknown"
rcon_startup_ready = False
rcon_startup_lock = threading.Lock()
RCON_STARTUP_READY_PATTERN = re.compile(
    r"Dedicated server took\s+\d+(?:[.,]\d+)?\s+seconds to load",
    re.IGNORECASE,
)
rcon_config_lock = threading.Lock()
rcon_cached_password = None
rcon_cached_port = RCON_PORT
rcon_cached_enabled = False
rcon_last_config_read_at = 0.0

# Shared dashboard metrics collector/broadcast state.
METRICS_COLLECT_INTERVAL_SECONDS = _cfg_int("METRICS_COLLECT_INTERVAL_SECONDS", 1, minimum=1)
METRICS_COLLECT_INTERVAL_OFF_SECONDS = _cfg_int("METRICS_COLLECT_INTERVAL_OFF_SECONDS", max(METRICS_COLLECT_INTERVAL_SECONDS, 5), minimum=1)
METRICS_STREAM_HEARTBEAT_SECONDS = _cfg_int("METRICS_STREAM_HEARTBEAT_SECONDS", 5, minimum=1)
LOG_STREAM_HEARTBEAT_SECONDS = _cfg_int("LOG_STREAM_HEARTBEAT_SECONDS", 5, minimum=1)
LOG_STREAM_EVENT_BUFFER_SIZE = _cfg_int("LOG_STREAM_EVENT_BUFFER_SIZE", 800, minimum=50)
MINECRAFT_LOG_TEXT_LIMIT = _cfg_int("MINECRAFT_LOG_TEXT_LIMIT", 1000, minimum=10)
BACKUP_LOG_TEXT_LIMIT = _cfg_int("BACKUP_LOG_TEXT_LIMIT", 200, minimum=10)
MCWEB_LOG_TEXT_LIMIT = _cfg_int("MCWEB_LOG_TEXT_LIMIT", 200, minimum=10)
MCWEB_ACTION_LOG_TEXT_LIMIT = _cfg_int("MCWEB_ACTION_LOG_TEXT_LIMIT", 200, minimum=10)
MINECRAFT_JOURNAL_TAIL_LINES = _cfg_int("MINECRAFT_JOURNAL_TAIL_LINES", 1000, minimum=10)
MINECRAFT_LOG_VISIBLE_LINES = _cfg_int("MINECRAFT_LOG_VISIBLE_LINES", 500, minimum=10)
HOME_PAGE_ACTIVE_TTL_SECONDS = _cfg_int("HOME_PAGE_ACTIVE_TTL_SECONDS", 30, minimum=1)
HOME_PAGE_HEARTBEAT_INTERVAL_MS = _cfg_int("HOME_PAGE_HEARTBEAT_INTERVAL_MS", 10000, minimum=1000)
FILE_PAGE_CACHE_REFRESH_SECONDS = _cfg_int("FILE_PAGE_CACHE_REFRESH_SECONDS", 15, minimum=1)
FILE_PAGE_ACTIVE_TTL_SECONDS = _cfg_int("FILE_PAGE_ACTIVE_TTL_SECONDS", 30, minimum=1)
FILE_PAGE_HEARTBEAT_INTERVAL_MS = _cfg_int("FILE_PAGE_HEARTBEAT_INTERVAL_MS", 10000, minimum=1000)
CRASH_STOP_GRACE_SECONDS = _cfg_int("CRASH_STOP_GRACE_SECONDS", 15, minimum=1)
BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS = _cfg_int("BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS", 15, minimum=1)
BACKUP_WATCH_INTERVAL_OFF_SECONDS = _cfg_int("BACKUP_WATCH_INTERVAL_OFF_SECONDS", max(BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS, 45), minimum=1)
BACKUP_WARNING_TTL_SECONDS = _cfg_float("BACKUP_WARNING_TTL_SECONDS", 120.0, minimum=1.0)
LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT = _cfg_float("LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT", 10.0, minimum=0.1)
STORAGE_SAFETY_CHECK_INTERVAL_ACTIVE_SECONDS = _cfg_int("STORAGE_SAFETY_CHECK_INTERVAL_ACTIVE_SECONDS", 5, minimum=1)
STORAGE_SAFETY_CHECK_INTERVAL_OFF_SECONDS = _cfg_int("STORAGE_SAFETY_CHECK_INTERVAL_OFF_SECONDS", 15, minimum=1)
SERVICE_STATUS_CACHE_ACTIVE_SECONDS = _cfg_float("SERVICE_STATUS_CACHE_ACTIVE_SECONDS", 1.0, minimum=0.0)
SERVICE_STATUS_CACHE_OFF_SECONDS = _cfg_float("SERVICE_STATUS_CACHE_OFF_SECONDS", 5.0, minimum=0.0)
SERVICE_STATUS_COMMAND_TIMEOUT_SECONDS = _cfg_float("SERVICE_STATUS_COMMAND_TIMEOUT_SECONDS", 3.0, minimum=0.5)
JOURNAL_LOAD_TIMEOUT_SECONDS = _cfg_float("JOURNAL_LOAD_TIMEOUT_SECONDS", 4.0, minimum=0.5)
RCON_STARTUP_JOURNAL_TIMEOUT_SECONDS = _cfg_float("RCON_STARTUP_JOURNAL_TIMEOUT_SECONDS", 4.0, minimum=0.5)
SLOW_METRICS_INTERVAL_ACTIVE_SECONDS = _cfg_float("SLOW_METRICS_INTERVAL_ACTIVE_SECONDS", 5.0, minimum=1.0)
SLOW_METRICS_INTERVAL_OFF_SECONDS = _cfg_float("SLOW_METRICS_INTERVAL_OFF_SECONDS", 15.0, minimum=1.0)
LOG_FETCHER_IDLE_SLEEP_SECONDS = _cfg_float("LOG_FETCHER_IDLE_SLEEP_SECONDS", 2.0, minimum=0.5)
CRASH_STOP_MARKERS = (
    "Preparing crash report with UUID",
    "This crash report has been saved to:",
)
metrics_collector_started = False
metrics_collector_start_lock = threading.Lock()
metrics_cache_cond = threading.Condition()
metrics_cache_seq = 0
metrics_cache_payload = {}
metrics_stream_client_count = 0
home_page_last_seen = 0.0
service_status_cache_lock = threading.Lock()
service_status_cache_value_ref = [""]
service_status_cache_at_ref = [0.0]
slow_metrics_lock = threading.Lock()
slow_metrics_cache = {}
slow_metrics_cache_status = ""
slow_metrics_cache_at = 0.0
backup_log_cache_lock = threading.Lock()
backup_log_cache_lines = deque(maxlen=BACKUP_LOG_TEXT_LIMIT)
backup_log_cache_loaded = False
backup_log_cache_mtime_ns = None
minecraft_log_cache_lock = threading.Lock()
minecraft_log_cache_lines = deque(maxlen=MINECRAFT_LOG_TEXT_LIMIT)
minecraft_log_cache_loaded = False
mcweb_log_cache_lock = threading.Lock()
mcweb_log_cache_lines = deque(maxlen=MCWEB_ACTION_LOG_TEXT_LIMIT)
mcweb_log_cache_loaded = False
mcweb_log_cache_mtime_ns = None
file_page_last_seen = 0.0
file_page_cache_refresher_started = False
file_page_cache_refresher_start_lock = threading.Lock()
file_page_cache_lock = threading.Lock()
file_page_cache = {
    "backups": {"items": [], "updated_at": 0.0},
    "crash_logs": {"items": [], "updated_at": 0.0},
    "minecraft_logs": {"items": [], "updated_at": 0.0},
}
crash_stop_lock = threading.Lock()
crash_stop_timer_active = False
restore_status_lock = threading.Lock()
restore_status = {
    "job_id": "",
    "running": False,
    "seq": 0,
    "events": [],
    "result": None,
    "undo_filename": "",
}
backup_warning_lock = threading.Lock()
backup_warning_seq = 0
backup_warning_message = ""
backup_warning_at = 0.0
storage_emergency_lock = threading.Lock()
storage_emergency_active = False
device_name_map_lock = threading.Lock()
device_name_map_cache = {}
device_name_map_mtime_ns_ref = [None]
log_stream_states = {
    source: {
        "cond": threading.Condition(),
        "seq": 0,
        "events": deque(maxlen=LOG_STREAM_EVENT_BUFFER_SIZE),
        "started": False,
        "lifecycle_lock": threading.Lock(),
        "clients": 0,
        "proc": None,
    }
    for source in LOG_SOURCE_KEYS
}

# Single-file HTML template for the dashboard UI.
HTML_TEMPLATE_NAME = "home.html"
FILES_TEMPLATE_NAME = "files.html"

# ----------------------------
# System and privilege helpers
# ----------------------------
_runtime_namespace = dict(globals())
RUNTIME_CONTEXT = _runtime_namespace
world_bindings = world_bindings_service.build_world_bindings(RUNTIME_CONTEXT)
_binding_stage_exports = set()
_binding_stage_values = {}


def _install_binding_stage(stage_name, mapping):
    """Install one binding stage and fail fast on duplicate exported keys."""
    duplicates = sorted(set(mapping.keys()) & _binding_stage_exports)
    if duplicates:
        raise KeyError(
            f"Duplicate binding keys in stage '{stage_name}': {', '.join(duplicates)}"
        )
    _binding_stage_exports.update(mapping.keys())
    _binding_stage_values.update(mapping)


_install_binding_stage("world_bindings", world_bindings)
world_bindings["_refresh_world_dir_from_server_properties"]()
_static_asset_version_fn = world_bindings["_static_asset_version"]


@app.context_processor
def inject_asset_helpers():
    """Expose per-file static version helper to templates.
Runtime helper inject_asset_helpers."""
    maintenance_enabled = True
    debug_page_visible = DEBUG_PAGE_VISIBLE
    if DEV_ENABLED:
        debug_page_visible = maintenance_enabled
    cleanup_has_missed = False
    try:
        non_normal_path = DATA_DIR / "cleanup_non_normal.txt"
        if non_normal_path.exists():
            payload = json.loads(non_normal_path.read_text(encoding="utf-8"))
            cleanup_has_missed = bool(payload.get("missed_runs"))
    except Exception:
        cleanup_has_missed = False
    return {
        "static_version": _static_asset_version_fn,
        "debug_enabled": DEBUG_ENABLED,
        "debug_page_visible": debug_page_visible,
        "maintenance_enabled": maintenance_enabled,
        "cleanup_has_missed": cleanup_has_missed,
    }


system_bindings = system_bindings_service.build_system_bindings(
    RUNTIME_CONTEXT,
    status_cache_service=status_cache_service,
    dashboard_runtime_service=dashboard_runtime_service,
    device_name_map_lookup=_device_name_map_lookup,
)
_install_binding_stage("system_bindings", system_bindings)

runtime_bindings = runtime_bindings_service.build_runtime_bindings(
    RUNTIME_CONTEXT,
    dashboard_runtime_service=dashboard_runtime_service,
    control_plane_service=control_plane_service,
    session_store_service=session_store_service,
    minecraft_runtime_service=minecraft_runtime_service,
    session_watchers_service=session_watchers_service,
)
_install_binding_stage("runtime_bindings", runtime_bindings)

request_bindings = request_bindings_service.build_request_bindings(
    session_store_service=session_store_service,
    session_state=session_state,
    initialize_session_tracking=runtime_bindings["initialize_session_tracking"],
    status_debug_note=runtime_bindings["_status_debug_note"],
    low_storage_error_message=runtime_bindings["low_storage_error_message"],
    users_file=USERS_FILE,
    users_file_lock=users_file_lock,
    display_tz=DISPLAY_TZ,
    get_device_name_map=system_bindings["get_device_name_map"],
)
_install_binding_stage("request_bindings", request_bindings)

debug_bindings = debug_bindings_service.build_debug_bindings(
    debug_tools_service=debug_tools_service,
    debug_enabled=DEBUG_ENABLED,
    debug_world_name=DEBUG_WORLD_NAME,
    debug_motd=DEBUG_MOTD,
    data_dir=DATA_DIR,
    app_dir=APP_DIR,
    service=SERVICE,
    backup_script=BACKUP_SCRIPT,
    backup_log_file=BACKUP_LOG_FILE,
    mcweb_action_log_file=MCWEB_ACTION_LOG_FILE,
    backup_state_file=BACKUP_STATE_FILE,
    session_file=SESSION_FILE,
    server_properties_candidates=SERVER_PROPERTIES_CANDIDATES,
    debug_server_properties_keys=DEBUG_SERVER_PROPERTIES_KEYS,
    debug_server_properties_forced_values=DEBUG_SERVER_PROPERTIES_FORCED_VALUES,
    debug_server_properties_int_keys=DEBUG_SERVER_PROPERTIES_INT_KEYS,
    debug_server_properties_bool_keys=DEBUG_SERVER_PROPERTIES_BOOL_KEYS,
    debug_server_properties_enums=DEBUG_SERVER_PROPERTIES_ENUMS,
    debug_env_lock=debug_env_lock,
    debug_env_original_values=debug_env_original_values,
    debug_env_overrides=debug_env_overrides,
    backup_state=backup_state,
    app=app,
    namespace=RUNTIME_CONTEXT,
    log_mcweb_log=log_mcweb_log,
    log_mcweb_exception=log_mcweb_exception,
    log_debug_page_action=log_debug_page_action,
    refresh_world_dir=world_bindings["_refresh_world_dir_from_server_properties"],
    refresh_rcon_config=runtime_bindings["_refresh_rcon_config"],
    invalidate_status_cache=system_bindings["invalidate_status_cache"],
    set_service_status_intent=runtime_bindings["set_service_status_intent"],
    write_session_start_time=runtime_bindings["write_session_start_time"],
    validate_sudo_password=runtime_bindings["validate_sudo_password"],
    record_successful_password_ip=request_bindings["record_successful_password_ip"],
    graceful_stop_minecraft=runtime_bindings["graceful_stop_minecraft"],
    clear_session_start_time=runtime_bindings["clear_session_start_time"],
    reset_backup_schedule_state=runtime_bindings["reset_backup_schedule_state"],
    run_backup_script=runtime_bindings["run_backup_script"],
)
_install_binding_stage("debug_bindings", debug_bindings)


def _binding(key):
    """Fetch a staged binding value and fail fast with context if missing."""
    if key not in _binding_stage_values:
        raise KeyError(f"Missing staged binding key: {key}")
    return _binding_stage_values[key]


# Apply all staged bindings at once to avoid fragile step-by-step global mutation.
RUNTIME_CONTEXT.update(_binding_stage_values)
app_lifecycle_service.install_flask_hooks(
    app,
    ensure_session_tracking_initialized=_binding("ensure_session_tracking_initialized"),
    ensure_metrics_collector_started=_binding("ensure_metrics_collector_started"),
    ensure_csrf_token=_binding("_ensure_csrf_token"),
    is_csrf_valid=_binding("_is_csrf_valid"),
    csrf_rejected_response=_binding("_csrf_rejected_response"),
    log_mcweb_action=log_mcweb_action,
    log_mcweb_exception=log_mcweb_exception,
)

# ----------------------------
# Flask routes
# ----------------------------
state_builder_service.assert_required_keys_present(RUNTIME_CONTEXT)
STATE = state_builder_service.build_app_state(RUNTIME_CONTEXT)
RUNTIME_CONTEXT["STATE"] = STATE
register_routes(app, STATE)

run_server = app_lifecycle_service.build_run_server(
    bootstrap_service=bootstrap_service,
    app=app,
    cfg_get_str=_cfg_str,
    cfg_get_int=_cfg_int,
    log_mcweb_log=log_mcweb_log,
    log_mcweb_exception=log_mcweb_exception,
    is_backup_running=_binding("is_backup_running"),
    load_backup_log_cache_from_disk=_binding("_load_backup_log_cache_from_disk"),
    prepare_debug_server_properties_bootup=_binding("prepare_debug_server_properties_bootup"),
    log_mcweb_boot_diagnostics=_binding("log_mcweb_boot_diagnostics"),
    load_minecraft_log_cache_from_journal=_binding("_load_minecraft_log_cache_from_journal"),
    load_mcweb_log_cache_from_disk=_binding("_load_mcweb_log_cache_from_disk"),
    ensure_session_tracking_initialized=_binding("ensure_session_tracking_initialized"),
    ensure_metrics_collector_started=_binding("ensure_metrics_collector_started"),
    collect_and_publish_metrics=_binding("_collect_and_publish_metrics"),
    start_idle_player_watcher=_binding("start_idle_player_watcher"),
    start_backup_session_watcher=_binding("start_backup_session_watcher"),
    start_storage_safety_watcher=_binding("start_storage_safety_watcher"),
)


if __name__ == "__main__":
    run_server()
