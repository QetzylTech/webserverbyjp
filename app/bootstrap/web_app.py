"""Web dashboard for controlling and monitoring a Minecraft systemd service.

This app provides:
- Service controls (start/stop/manual backup)
- Live server and Minecraft stats
- Systemd log viewer
- Automatic idle shutdown and session-based backup scheduling"""
from flask import Flask, abort, redirect, request
from pathlib import Path
import threading
import re
import json
import os
import secrets
import sys
import time
from collections import deque
from zoneinfo import ZoneInfo
from app.core.config import apply_default_flask_config, resolve_secret_key
from app.core.filesystem_utils import (
    list_download_files as _list_download_files,
    read_recent_file_lines as _read_recent_file_lines,
    safe_file_mtime_ns as _safe_file_mtime_ns,
    safe_filename_in_dir as _safe_filename_in_dir,
)
from app.core.logging_setup import build_loggers
from app.infrastructure.adapters import PlatformServiceControlAdapter
from app.bootstrap.config_loader import load_web_config
from app.bootstrap.container import build_runtime_bundle
from app.services import service_ops as service_ops
from app.services import data_bootstrap as data_bootstrap_service
from app.services import setup_service as setup_service
from app.services import setup_orchestration as setup_orchestration_service
from app.services import bootstrap as bootstrap_service
from app.services import app_lifecycle as app_lifecycle_service
from app.services import dashboard_runtime as dashboard_runtime_service
from app.services import minecraft_runtime as minecraft_runtime_service
from app.services import request_bindings as request_bindings_service
from app.services import runtime_bindings as runtime_bindings_service
from app.services import session_store as session_store_service
from app.services import session_watchers as session_watchers_service
from app.services import state_builder as state_builder_service
from app.services import runtime_wiring as runtime_wiring_service
from app.services import status_cache as status_cache_service
from app.services import worker_runtime as worker_runtime_service
from app.services.worker_scheduler import start_detached
from app.services import system_bindings as system_bindings_service
from app.services.system_metrics import (
    get_cpu_frequency,
    get_cpu_usage_per_core,
    get_ram_usage,
    get_storage_usage,
)
from app.services import world_bindings as world_bindings_service
from app.routes.dashboard_routes import register_routes
from app.routes.setup_routes import register_setup_routes
from app.state import BackupState, SessionState, REQUIRED_STATE_KEY_SET

APP_DIR = Path(__file__).resolve().parent.parent
app = Flask(
    __name__,
    template_folder=str(APP_DIR / "templates"),
    static_folder=str(APP_DIR / "static"),
)
_platform_service_control = PlatformServiceControlAdapter()
APP_CONFIG = load_web_config(
    APP_DIR,
    default_backup_dir=Path(_platform_service_control.default_backup_dir()),
    default_minecraft_root=Path(_platform_service_control.default_minecraft_root()),
)
WEB_CONF_PATH = APP_CONFIG.web_conf_path
_WEB_CFG_VALUES = APP_CONFIG.raw_values
STATE = None

_setup_status = setup_service.assess_setup_requirement(WEB_CONF_PATH, _WEB_CFG_VALUES)
SETUP_REQUIRED_STATE = {
    "required": bool(_setup_status.get("required")),
    "reasons": list(_setup_status.get("reasons", [])),
    "mode": str(_setup_status.get("mode", "full") or "full"),
}
if SETUP_REQUIRED_STATE["required"]:
    app.config["SECRET_KEY"] = secrets.token_hex(32)
else:
    app.config["SECRET_KEY"] = resolve_secret_key(lambda _k, _d="": APP_CONFIG.secret_key_value or _d, "MCWEB_SECRET_KEY")
apply_default_flask_config(app)

# Core service and application settings.
FAVICON_URL = "https://static.wikia.nocookie.net/logopedia/images/e/e3/Minecraft_Launcher.svg/revision/latest/scale-to-width-down/250?cb=20230616222246"
SERVICE = APP_CONFIG.service
ADMIN_PASSWORD_HASH = APP_CONFIG.admin_password_hash
BACKUP_SCRIPT = APP_DIR / "scripts" / "backup.sh"
BACKUP_DIR = APP_CONFIG.backup_dir
MINECRAFT_ROOT_DIR = APP_CONFIG.minecraft_root_dir
WORLD_DIR = MINECRAFT_ROOT_DIR / "config"
CRASH_REPORTS_DIR = MINECRAFT_ROOT_DIR / "crash-reports"
MINECRAFT_LOGS_DIR = MINECRAFT_ROOT_DIR / "logs"
MCWEB_LOG_DIR = APP_DIR / "logs"
BACKUP_LOG_FILE = MCWEB_LOG_DIR / "backup.log"
MCWEB_ACTION_LOG_FILE = MCWEB_LOG_DIR / "mcweb_actions.log"
MCWEB_LOG_FILE = MCWEB_LOG_DIR / "mcweb.log"
DATA_DIR = APP_DIR / "data"
# Structured runtime state always lives beside mcweb.py under ./data.
APP_STATE_DB_PATH = APP_DIR / "data" / "app_state.sqlite3"
DOCS_DIR = APP_DIR / "doc"
BACKUP_STATE_FILE = DATA_DIR / "state.txt"
SESSION_FILE = DATA_DIR / "session.txt"
DOC_README_URL = APP_CONFIG.doc_readme_url
DEVICE_MAP_CSV_PATH = APP_CONFIG.device_map_csv_path
# "PST" here refers to Philippines Standard Time (UTC+8), not Pacific Time.
_display_tz_name = APP_CONFIG.display_tz_name
try:
    DISPLAY_TZ = ZoneInfo(_display_tz_name)
except Exception:
    _display_tz_name = "Asia/Manila"
    DISPLAY_TZ = ZoneInfo("Asia/Manila")
# Force process timezone so subprocess logs align with DISPLAY_TZ.
os.environ["TZ"] = _display_tz_name
if hasattr(time, "tzset"):
    try:
        time.tzset()
    except Exception:
        pass
log_mcweb_action, log_mcweb_log, log_mcweb_exception = build_loggers(
    DISPLAY_TZ,
    MCWEB_LOG_DIR,
    MCWEB_ACTION_LOG_FILE,
    MCWEB_LOG_FILE,
)


def _setup_required():
    return bool(SETUP_REQUIRED_STATE.get("required"))


def _setup_mode():
    return str(SETUP_REQUIRED_STATE.get("mode", "full") or "full")


def _trigger_process_reload():
    def _reload():
        time.sleep(0.35)
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as exc:
            log_mcweb_exception("setup/reload", exc)

    start_detached(target=_reload, daemon=True)


@app.before_request
def _setup_route_guard():
    setup_mode = _setup_required()
    path = request.path or ""
    if setup_mode:
        if path == "/setup" or path.startswith("/setup") or path.startswith("/static/") or path == "/sw.js":
            return None
        if path == "/favicon.ico":
            return None
        return redirect("/setup")
    if path == "/setup" or path.startswith("/setup"):
        return abort(404)
MAINTENANCE_SCOPE_BACKUP_ZIP = APP_CONFIG.maintenance_scope_backup_zip
MAINTENANCE_SCOPE_STALE_WORLD_DIR = APP_CONFIG.maintenance_scope_stale_world_dir
MAINTENANCE_SCOPE_OLD_WORLD_ZIP = APP_CONFIG.maintenance_scope_old_world_zip
# Hard safety guards are intentionally fixed and not env-configurable.
MAINTENANCE_GUARD_NEVER_DELETE_NEWEST_N = 1
MAINTENANCE_GUARD_NEVER_DELETE_LAST_BACKUP = True
MAINTENANCE_GUARD_PROTECT_ACTIVE_WORLD = True
RCON_HOST = "127.0.0.1"
RCON_PORT = 25575
SERVER_PROPERTIES_CANDIDATES = [
    MINECRAFT_ROOT_DIR / "server.properties",
    MINECRAFT_ROOT_DIR / "server" / "server.properties",
    APP_DIR / "server.properties",
    APP_DIR.parent / "server.properties",
]
data_bootstrap_service.ensure_data_bootstrap(
    data_dir=DATA_DIR,
    app_state_db_path=APP_STATE_DB_PATH,
    log_mcweb_log=log_mcweb_log,
    log_mcweb_exception=log_mcweb_exception,
)


def _setup_defaults():
    return setup_service.setup_form_defaults(_WEB_CFG_VALUES)


def _save_setup_values(values):
    return setup_orchestration_service.save_setup_values(
        values,
        setup_service=setup_service,
        data_bootstrap_service=data_bootstrap_service,
        web_conf_path=WEB_CONF_PATH,
        data_dir=DATA_DIR,
        app_state_db_path=APP_STATE_DB_PATH,
        setup_required_state=SETUP_REQUIRED_STATE,
        trigger_process_reload=_trigger_process_reload,
        log_mcweb_log=log_mcweb_log,
        log_mcweb_exception=log_mcweb_exception,
    )


register_setup_routes(
    app,
    is_setup_required=_setup_required,
    setup_mode=_setup_mode,
    setup_defaults=_setup_defaults,
    save_setup_values=_save_setup_values,
)

# Backup and automation timing controls.
BACKUP_INTERVAL_HOURS = APP_CONFIG.backup_interval_hours
BACKUP_INTERVAL_SECONDS = max(60, int(BACKUP_INTERVAL_HOURS * 3600))
IDLE_ZERO_PLAYERS_SECONDS = APP_CONFIG.idle_zero_players_seconds
IDLE_CHECK_INTERVAL_SECONDS = APP_CONFIG.idle_check_interval_seconds
IDLE_CHECK_INTERVAL_ACTIVE_SECONDS = APP_CONFIG.idle_check_interval_active_seconds
IDLE_CHECK_INTERVAL_OFF_SECONDS = APP_CONFIG.idle_check_interval_off_seconds

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

OFF_STATES = {"inactive", "failed"}
LOG_SOURCE_KEYS = ("minecraft", "backup", "mcweb", "mcweb_log")

# Cache Minecraft runtime probes so rapid UI polling does not overwhelm RCON.
MC_QUERY_INTERVAL_SECONDS = APP_CONFIG.mc_query_interval_seconds
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
METRICS_COLLECT_INTERVAL_SECONDS = APP_CONFIG.metrics_collect_interval_seconds
METRICS_COLLECT_INTERVAL_OFF_SECONDS = APP_CONFIG.metrics_collect_interval_off_seconds
METRICS_STREAM_HEARTBEAT_SECONDS = APP_CONFIG.metrics_stream_heartbeat_seconds
LOG_STREAM_HEARTBEAT_SECONDS = APP_CONFIG.log_stream_heartbeat_seconds
LOG_STREAM_EVENT_BUFFER_SIZE = APP_CONFIG.log_stream_event_buffer_size
MINECRAFT_LOG_TEXT_LIMIT = APP_CONFIG.minecraft_log_text_limit
BACKUP_LOG_TEXT_LIMIT = APP_CONFIG.backup_log_text_limit
MCWEB_LOG_TEXT_LIMIT = APP_CONFIG.mcweb_log_text_limit
MCWEB_ACTION_LOG_TEXT_LIMIT = APP_CONFIG.mcweb_action_log_text_limit
MINECRAFT_JOURNAL_TAIL_LINES = APP_CONFIG.minecraft_journal_tail_lines
MINECRAFT_LOG_VISIBLE_LINES = APP_CONFIG.minecraft_log_visible_lines
HOME_PAGE_ACTIVE_TTL_SECONDS = APP_CONFIG.home_page_active_ttl_seconds
HOME_PAGE_HEARTBEAT_INTERVAL_MS = APP_CONFIG.home_page_heartbeat_interval_ms
FILE_PAGE_CACHE_REFRESH_SECONDS = APP_CONFIG.file_page_cache_refresh_seconds
FILE_PAGE_ACTIVE_TTL_SECONDS = APP_CONFIG.file_page_active_ttl_seconds
FILE_PAGE_HEARTBEAT_INTERVAL_MS = APP_CONFIG.file_page_heartbeat_interval_ms
CRASH_STOP_GRACE_SECONDS = APP_CONFIG.crash_stop_grace_seconds
BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS = APP_CONFIG.backup_watch_interval_active_seconds
BACKUP_WATCH_INTERVAL_OFF_SECONDS = APP_CONFIG.backup_watch_interval_off_seconds
BACKUP_WARNING_TTL_SECONDS = APP_CONFIG.backup_warning_ttl_seconds
LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT = APP_CONFIG.low_storage_available_threshold_percent
STORAGE_SAFETY_CHECK_INTERVAL_ACTIVE_SECONDS = APP_CONFIG.storage_safety_check_interval_active_seconds
STORAGE_SAFETY_CHECK_INTERVAL_OFF_SECONDS = APP_CONFIG.storage_safety_check_interval_off_seconds
OPERATION_RECONCILE_INTERVAL_SECONDS = APP_CONFIG.operation_reconcile_interval_seconds
OPERATION_INTENT_STALE_SECONDS = APP_CONFIG.operation_intent_stale_seconds
OPERATION_START_TIMEOUT_SECONDS = APP_CONFIG.operation_start_timeout_seconds
OPERATION_STOP_TIMEOUT_SECONDS = APP_CONFIG.operation_stop_timeout_seconds
OPERATION_RESTORE_TIMEOUT_SECONDS = APP_CONFIG.operation_restore_timeout_seconds
SERVICE_STATUS_CACHE_ACTIVE_SECONDS = APP_CONFIG.service_status_cache_active_seconds
SERVICE_STATUS_CACHE_OFF_SECONDS = APP_CONFIG.service_status_cache_off_seconds
SERVICE_STATUS_COMMAND_TIMEOUT_SECONDS = APP_CONFIG.service_status_command_timeout_seconds
JOURNAL_LOAD_TIMEOUT_SECONDS = APP_CONFIG.journal_load_timeout_seconds
RCON_STARTUP_JOURNAL_TIMEOUT_SECONDS = APP_CONFIG.rcon_startup_journal_timeout_seconds
SLOW_METRICS_INTERVAL_ACTIVE_SECONDS = APP_CONFIG.slow_metrics_interval_active_seconds
SLOW_METRICS_INTERVAL_OFF_SECONDS = APP_CONFIG.slow_metrics_interval_off_seconds
LOG_FETCHER_IDLE_SLEEP_SECONDS = APP_CONFIG.log_fetcher_idle_sleep_seconds
CRASH_STOP_MARKERS = (
    "Preparing crash report with UUID",
    "This crash report has been saved to:",
)
PROCESS_ROLE = APP_CONFIG.process_role
DEBUG_APP_HOST = APP_CONFIG.debug_app_host
DEBUG_APP_PORT = APP_CONFIG.debug_app_port
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
operation_reconciler_started = False
operation_reconciler_start_lock = threading.Lock()
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
_runtime_bundle = build_runtime_bundle(
    runtime_wiring_service=runtime_wiring_service,
    app=app,
    namespace=locals(),
    required_state_key_set=REQUIRED_STATE_KEY_SET,
    runtime_context_extra_keys=_RUNTIME_CONTEXT_EXTRA_KEYS,
    runtime_imported_symbols=_RUNTIME_IMPORTED_SYMBOLS,
    world_bindings_service=world_bindings_service,
    system_bindings_service=system_bindings_service,
    runtime_bindings_service=runtime_bindings_service,
    request_bindings_service=request_bindings_service,
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
if _setup_required():
    def _setup_run_server():
        bootstrap_service.run_server(
            app,
            APP_CONFIG,
            log_mcweb_log,
            log_mcweb_exception,
            boot_steps=[],
        )
    run_server = _setup_run_server


def run_worker():
    """Run background worker loops without starting the Flask web server."""
    log_mcweb_log("worker-boot-start", command=f"role={PROCESS_ROLE}")
    ctx = RUNTIME_CONTEXT
    boot_steps = [
        ("ensure_session_tracking_initialized", ctx["ensure_session_tracking_initialized"]),
        ("start_worker_loops", lambda: worker_runtime_service.start_worker_loops(STATE)),
    ]
    for step_name, step_func in boot_steps:
        try:
            step_func()
        except Exception as exc:
            log_mcweb_exception(f"worker_step/{step_name}", exc)
            raise
    log_mcweb_log("worker-boot-ready", command="background loops active")
    while True:
        # Keep worker process alive while daemon loops run.
        time.sleep(60)


@app.context_processor
def inject_asset_helpers():
    """Expose per-file static version helper to templates.
Runtime helper inject_asset_helpers."""
    maintenance_enabled = True
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
        "maintenance_enabled": maintenance_enabled,
        "cleanup_has_missed": cleanup_has_missed,
    }


if __name__ == "__main__":
    if PROCESS_ROLE == "worker":
        run_worker()
    else:
        run_server()
