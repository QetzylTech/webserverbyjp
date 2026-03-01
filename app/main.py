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
import os
import time
from collections import deque
from zoneinfo import ZoneInfo
from app.core.config import apply_default_flask_config, resolve_secret_key
from app.core.device_map import get_device_name_map as _device_name_map_lookup
from app.core import state_store as state_store_service
from app.core.filesystem_utils import (
    list_download_files as _list_download_files,
    read_recent_file_lines as _read_recent_file_lines,
    safe_file_mtime_ns as _safe_file_mtime_ns,
    safe_filename_in_dir as _safe_filename_in_dir,
)
from app.core.logging_setup import build_loggers
from app.core.web_config import WebConfig
from app.services import service_ops as service_ops
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
from app.services import runtime_wiring as runtime_wiring_service
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
from app.state import BackupState, SessionState, REQUIRED_STATE_KEY_SET

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
BACKUP_SCRIPT = APP_DIR / "scripts" / "backup.sh"
BACKUP_DIR = _cfg_path("BACKUP_DIR", Path("/home/marites/backups"))
MINECRAFT_ROOT_DIR = _cfg_path("MINECRAFT_ROOT_DIR", Path("/opt/Minecraft"))
WORLD_DIR = MINECRAFT_ROOT_DIR / "config"
CRASH_REPORTS_DIR = MINECRAFT_ROOT_DIR / "crash-reports"
MINECRAFT_LOGS_DIR = MINECRAFT_ROOT_DIR / "logs"
MCWEB_LOG_DIR = APP_DIR / "logs"
BACKUP_LOG_FILE = MCWEB_LOG_DIR / "backup.log"
MCWEB_ACTION_LOG_FILE = MCWEB_LOG_DIR / "mcweb_actions.log"
MCWEB_LOG_FILE = MCWEB_LOG_DIR / "mcweb.log"
DEBUG_PAGE_LOG_FILE = MCWEB_LOG_DIR / "debug_page.log"
DATA_DIR = APP_DIR / "data"
# Structured runtime state always lives beside mcweb.py under ./data.
APP_STATE_DB_PATH = APP_DIR / "data" / "app_state.sqlite3"
LEGACY_APP_STATE_DB_PATH = APP_DIR / "app_state.sqlite3"
DOCS_DIR = APP_DIR / "doc"
BACKUP_STATE_FILE = DATA_DIR / "state.txt"
SESSION_FILE = DATA_DIR / "session.txt"
DOC_README_URL = _cfg_str("DOC_README_URL", "/doc/server_setup_doc.md")
DEVICE_MAP_CSV_PATH = _cfg_path(
    "DEVICE_MAP_CSV_PATH",
    DATA_DIR / "marites.minecraft@gmail.com-devices-2026-02-26T04-37-44-487Z.csv",
)
# "PST" here refers to Philippines Standard Time (UTC+8), not Pacific Time.
_display_tz_name = _cfg_str("DISPLAY_TZ", "Asia/Manila")
try:
    DISPLAY_TZ = ZoneInfo(_display_tz_name)
except Exception:
    _display_tz_name = "Asia/Manila"
    DISPLAY_TZ = ZoneInfo("Asia/Manila")
# Force process timezone so subprocess logs (journalctl/date-driven scripts) align with DISPLAY_TZ.
os.environ["TZ"] = _display_tz_name
if hasattr(time, "tzset"):
    try:
        time.tzset()
    except Exception:
        pass
log_mcweb_action, log_mcweb_log, log_mcweb_exception, log_debug_page_action = build_loggers(
    DISPLAY_TZ,
    MCWEB_LOG_DIR,
    MCWEB_ACTION_LOG_FILE,
    MCWEB_LOG_FILE,
    DEBUG_PAGE_LOG_FILE,
)
DEBUG_ENABLED = _cfg_str("DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
DEBUG_PAGE_VISIBLE = DEBUG_ENABLED
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
    MINECRAFT_ROOT_DIR / "server.properties",
    MINECRAFT_ROOT_DIR / "server" / "server.properties",
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
state_store_service.migrate_state_db_to_data_dir(
    db_path=APP_STATE_DB_PATH,
    legacy_paths=(LEGACY_APP_STATE_DB_PATH,),
    log_exception=log_mcweb_exception,
)
state_store_service.initialize_state_db(
    db_path=APP_STATE_DB_PATH,
    log_exception=log_mcweb_exception,
)
DEBUG_SERVER_PROPERTIES_FORCED_VALUES = {
    "level-name": DEBUG_WORLD_NAME,
    "enable-rcon": "true",
    "rcon.password": "SuperCute",
    "rcon.port": str(RCON_PORT),
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

_RUNTIME_CONTEXT_EXTRA_KEYS = frozenset({
    "APP_DIR",
    "APP_STATE_DB_PATH",
    "STATE",
})
_RUNTIME_IMPORTED_SYMBOLS = {
    "_list_download_files": _list_download_files,
    "_read_recent_file_lines": _read_recent_file_lines,
    "_safe_file_mtime_ns": _safe_file_mtime_ns,
    "_safe_filename_in_dir": _safe_filename_in_dir,
    "get_cpu_frequency": get_cpu_frequency,
    "get_cpu_usage_per_core": get_cpu_usage_per_core,
    "get_ram_usage": get_ram_usage,
    "get_storage_usage": get_storage_usage,
}


def _build_runtime_context(namespace):
    """Build explicit runtime context from known state keys only."""
    allowed = REQUIRED_STATE_KEY_SET | _RUNTIME_CONTEXT_EXTRA_KEYS
    context = {key: namespace[key] for key in allowed if key in namespace}
    context.update(_RUNTIME_IMPORTED_SYMBOLS)
    context.setdefault("STATE", None)
    return context


RUNTIME_CONTEXT = _build_runtime_context(locals())
_runtime_bundle = runtime_wiring_service.create_runtime(
    app=app,
    namespace=locals(),
    required_state_key_set=REQUIRED_STATE_KEY_SET,
    runtime_context_extra_keys=_RUNTIME_CONTEXT_EXTRA_KEYS,
    runtime_imported_symbols=_RUNTIME_IMPORTED_SYMBOLS,
    world_bindings_service=world_bindings_service,
    system_bindings_service=system_bindings_service,
    runtime_bindings_service=runtime_bindings_service,
    request_bindings_service=request_bindings_service,
    debug_bindings_service=debug_bindings_service,
    debug_tools_service=debug_tools_service,
    state_builder_service=state_builder_service,
    app_lifecycle_service=app_lifecycle_service,
    session_store_service=session_store_service,
    minecraft_runtime_service=minecraft_runtime_service,
    session_watchers_service=session_watchers_service,
    control_plane_service=service_ops,
    dashboard_runtime_service=dashboard_runtime_service,
    status_cache_service=status_cache_service,
    register_routes=register_routes,
)
RUNTIME_CONTEXT = _runtime_bundle["runtime_context"]
STATE = _runtime_bundle["state"]
_static_asset_version_fn = _runtime_bundle["static_asset_version_fn"]
run_server = _runtime_bundle["run_server"]


@app.context_processor
def inject_asset_helpers():
    """Expose per-file static version helper to templates.
Runtime helper inject_asset_helpers."""
    maintenance_enabled = True
    debug_page_visible = DEBUG_PAGE_VISIBLE
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


if __name__ == "__main__":
    run_server()
