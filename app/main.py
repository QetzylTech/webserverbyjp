"""Web dashboard for controlling and monitoring a Minecraft systemd service.

This app provides:
- Service controls (start/stop/manual backup)
- Live server and Minecraft stats
- Systemd log viewer
- Automatic idle shutdown and session-based backup scheduling
"""

from flask import Flask, render_template, redirect, request, jsonify, Response, stream_with_context, session, has_request_context, send_from_directory, abort
import subprocess
from pathlib import Path
from datetime import datetime
import time
import threading
import re
import json
import os
import secrets
from collections import deque
from zoneinfo import ZoneInfo
from app.core.web_config import WebConfig
from app.core.filesystem_utils import (
    list_download_files as _list_download_files,
    read_recent_file_lines as _read_recent_file_lines,
    safe_file_mtime_ns as _safe_file_mtime_ns,
    safe_filename_in_dir as _safe_filename_in_dir,
)
from app.services.system_metrics import (
    get_cpu_usage_per_core,
    get_ram_usage,
    get_cpu_frequency,
    get_storage_usage,
)
from app.services import control_plane as control_plane_service
from app.services import dashboard_runtime as dashboard_runtime_service
from app.services import minecraft_runtime as minecraft_runtime_service
from app.services import session_watchers as session_watchers_service
from app.core.action_logging import make_log_action, make_log_exception
from app.routes.dashboard_routes import register_routes
from app.state import AppState, BackupState, SessionState

APP_DIR = Path(__file__).resolve().parent.parent
app = Flask(
    __name__,
    template_folder=str(APP_DIR / "templates"),
    static_folder=str(APP_DIR / "static"),
)
WEB_CONF_PATH = APP_DIR / "mcweb.env"
_WEB_CFG = WebConfig(WEB_CONF_PATH, APP_DIR)
_cfg_str = _WEB_CFG.get_str
_cfg_int = _WEB_CFG.get_int
_cfg_float = _WEB_CFG.get_float
_cfg_path = _WEB_CFG.get_path
STATE = None

app.config["SECRET_KEY"] = (
    os.environ.get("MCWEB_SECRET_KEY")
    or os.environ.get("FLASK_SECRET_KEY")
    or _cfg_str("MCWEB_SECRET_KEY", "")
    or secrets.token_hex(32)
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

# Core service and application settings.
FAVICON_URL = "https://static.wikia.nocookie.net/logopedia/images/e/e3/Minecraft_Launcher.svg/revision/latest/scale-to-width-down/250?cb=20230616222246"
SERVICE = _cfg_str("SERVICE", "minecraft")
BACKUP_SCRIPT = _cfg_path("BACKUP_SCRIPT", APP_DIR / "scripts" / "backup.sh")
BACKUP_DIR = _cfg_path("BACKUP_DIR", Path("/home/marites/backups"))
CRASH_REPORTS_DIR = _cfg_path("CRASH_REPORTS_DIR", APP_DIR.parent / "crash-reports")
MINECRAFT_LOGS_DIR = _cfg_path("MINECRAFT_LOGS_DIR", APP_DIR.parent / "logs")
MCWEB_LOG_DIR = _cfg_path("MCWEB_LOG_DIR", APP_DIR / "logs")
BACKUP_LOG_FILE = MCWEB_LOG_DIR / "backup.log"
MCWEB_ACTION_LOG_FILE = MCWEB_LOG_DIR / "mcweb-actions.log"
DATA_DIR = _cfg_path("DATA_DIR", APP_DIR / "data")
DOCS_DIR = _cfg_path("DOCS_DIR", APP_DIR / "doc")
BACKUP_STATE_FILE = _cfg_path("BACKUP_STATE_FILE", DATA_DIR / "state.txt")
SESSION_FILE = DATA_DIR / "session.txt"
DOC_README_URL = _cfg_str("DOC_README_URL", "/doc/server_setup_doc.md")
# "PST" here refers to Philippines Standard Time (UTC+8), not Pacific Time.
try:
    DISPLAY_TZ = ZoneInfo(_cfg_str("DISPLAY_TZ", "Asia/Manila"))
except Exception:
    DISPLAY_TZ = ZoneInfo("Asia/Manila")
log_mcweb_action = make_log_action(DISPLAY_TZ, MCWEB_LOG_DIR, MCWEB_ACTION_LOG_FILE)
log_mcweb_exception = make_log_exception(log_mcweb_action)
RCON_HOST = _cfg_str("RCON_HOST", "127.0.0.1")
RCON_PORT = _cfg_int("RCON_PORT", 25575, minimum=1)
SERVER_PROPERTIES_CANDIDATES = [
    Path("/opt/Minecraft/server.properties"),
    Path("/opt/Minecraft/server/server.properties"),
    APP_DIR / "server.properties",
    APP_DIR.parent / "server.properties",
]

def _static_asset_version(filename):
    # Version token for static assets based on each file's mtime.
    try:
        path = APP_DIR / "static" / filename
        return int(path.stat().st_mtime)
    except OSError:
        return 0

@app.context_processor
def inject_asset_helpers():
    # Expose per-file static version helper to templates.
    return {"static_version": _static_asset_version}

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

OFF_STATES = {"inactive", "failed"}
LOG_SOURCE_KEYS = ("minecraft", "backup", "mcweb")

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
HOME_PAGE_ACTIVE_TTL_SECONDS = _cfg_int("HOME_PAGE_ACTIVE_TTL_SECONDS", 30, minimum=1)
HOME_PAGE_HEARTBEAT_INTERVAL_MS = _cfg_int("HOME_PAGE_HEARTBEAT_INTERVAL_MS", 10000, minimum=1000)
FILE_PAGE_CACHE_REFRESH_SECONDS = _cfg_int("FILE_PAGE_CACHE_REFRESH_SECONDS", 15, minimum=1)
FILE_PAGE_ACTIVE_TTL_SECONDS = _cfg_int("FILE_PAGE_ACTIVE_TTL_SECONDS", 30, minimum=1)
FILE_PAGE_HEARTBEAT_INTERVAL_MS = _cfg_int("FILE_PAGE_HEARTBEAT_INTERVAL_MS", 10000, minimum=1000)
LOG_STREAM_EVENT_BUFFER_SIZE = 800
CRASH_STOP_GRACE_SECONDS = _cfg_int("CRASH_STOP_GRACE_SECONDS", 15, minimum=1)
BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS = _cfg_int("BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS", 15, minimum=1)
BACKUP_WATCH_INTERVAL_OFF_SECONDS = _cfg_int("BACKUP_WATCH_INTERVAL_OFF_SECONDS", max(BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS, 45), minimum=1)
SERVICE_STATUS_CACHE_ACTIVE_SECONDS = _cfg_float("SERVICE_STATUS_CACHE_ACTIVE_SECONDS", 1.0, minimum=0.0)
SERVICE_STATUS_CACHE_OFF_SECONDS = _cfg_float("SERVICE_STATUS_CACHE_OFF_SECONDS", 5.0, minimum=0.0)
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
service_status_cache_value = ""
service_status_cache_at = 0.0
slow_metrics_lock = threading.Lock()
slow_metrics_cache = {}
slow_metrics_cache_status = ""
slow_metrics_cache_at = 0.0
backup_log_cache_lock = threading.Lock()
backup_log_cache_lines = deque(maxlen=200)
backup_log_cache_loaded = False
backup_log_cache_mtime_ns = None
minecraft_log_cache_lock = threading.Lock()
minecraft_log_cache_lines = deque(maxlen=1000)
minecraft_log_cache_loaded = False
mcweb_log_cache_lock = threading.Lock()
mcweb_log_cache_lines = deque(maxlen=200)
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
def get_status():
    # Return the raw systemd state for the Minecraft service.
    global service_status_cache_value
    global service_status_cache_at
    now = time.time()
    with service_status_cache_lock:
        cached = service_status_cache_value
        cached_at = service_status_cache_at
    if cached:
        ttl = SERVICE_STATUS_CACHE_ACTIVE_SECONDS if cached == "active" else SERVICE_STATUS_CACHE_OFF_SECONDS
        if ttl > 0 and (now - cached_at) <= ttl:
            return cached

    result = subprocess.run(
        ["systemctl", "is-active", SERVICE],
        capture_output=True, text=True
    )
    status = result.stdout.strip()
    with service_status_cache_lock:
        service_status_cache_value = status
        service_status_cache_at = now
    return status

def invalidate_status_cache():
    # Force next get_status() call to hit systemd.
    global service_status_cache_value
    global service_status_cache_at
    with service_status_cache_lock:
        service_status_cache_value = ""
        service_status_cache_at = 0.0

def _load_backup_log_cache_from_disk():
    return dashboard_runtime_service.load_backup_log_cache_from_disk(STATE)

def _append_backup_log_cache_line(line):
    return dashboard_runtime_service.append_backup_log_cache_line(STATE, line)

def _get_cached_backup_log_text():
    return dashboard_runtime_service.get_cached_backup_log_text(STATE)

def _load_minecraft_log_cache_from_journal():
    return dashboard_runtime_service.load_minecraft_log_cache_from_journal(STATE)

def _append_minecraft_log_cache_line(line):
    return dashboard_runtime_service.append_minecraft_log_cache_line(STATE, line)

def _get_cached_minecraft_log_text():
    return dashboard_runtime_service.get_cached_minecraft_log_text(STATE)

def _load_mcweb_log_cache_from_disk():
    return dashboard_runtime_service.load_mcweb_log_cache_from_disk(STATE)

def _append_mcweb_log_cache_line(line):
    return dashboard_runtime_service.append_mcweb_log_cache_line(STATE, line)

def _get_cached_mcweb_log_text():
    return dashboard_runtime_service.get_cached_mcweb_log_text(STATE)

def _mark_file_page_client_active():
    return dashboard_runtime_service.mark_file_page_client_active(STATE)

def get_cached_file_page_items(cache_key):
    return dashboard_runtime_service.get_cached_file_page_items(STATE, cache_key)

def file_page_cache_refresher_loop():
    return dashboard_runtime_service.file_page_cache_refresher_loop(STATE)

def ensure_file_page_cache_refresher_started():
    return dashboard_runtime_service.ensure_file_page_cache_refresher_started(STATE)

def _detect_server_properties_path():
    # Return first readable server.properties candidate, if any.
    for path in SERVER_PROPERTIES_CANDIDATES:
        if path.exists():
            return path
    return None

def log_mcweb_boot_diagnostics():
    # Log boot-time file/config detection snapshot.
    try:
        server_props = _detect_server_properties_path()
        _, rcon_port, rcon_enabled = _refresh_rcon_config()
        details = (
            f"service={SERVICE}; "
            f"backup_script={BACKUP_SCRIPT} exists={BACKUP_SCRIPT.exists()}; "
            f"backup_log={BACKUP_LOG_FILE} exists={BACKUP_LOG_FILE.exists()}; "
            f"mcweb_action_log={MCWEB_ACTION_LOG_FILE}; "
            f"state_file={BACKUP_STATE_FILE} exists={BACKUP_STATE_FILE.exists()}; "
            f"session_file={SESSION_FILE} exists={SESSION_FILE.exists()}; "
            f"server_properties={(server_props if server_props else 'missing')}; "
            f"rcon_enabled={rcon_enabled}; rcon_port={rcon_port}"
        )
        log_mcweb_action("boot", command=details)
    except Exception as exc:
        log_mcweb_exception("boot_diagnostics", exc)

def set_service_status_intent(intent):
    return control_plane_service.set_service_status_intent(STATE, intent)

def get_service_status_intent():
    return control_plane_service.get_service_status_intent(STATE)

def stop_service_systemd():
    return control_plane_service.stop_service_systemd(STATE)

def get_sudo_password():
    return control_plane_service.get_sudo_password(STATE)


def run_sudo(cmd):
    return control_plane_service.run_sudo(STATE, cmd)


def validate_sudo_password(sudo_password):
    return control_plane_service.validate_sudo_password(STATE, sudo_password)

def ensure_session_file():
    return control_plane_service.ensure_session_file(STATE)

def read_session_start_time():
    return control_plane_service.read_session_start_time(STATE)

def write_session_start_time(timestamp=None):
    return control_plane_service.write_session_start_time(STATE, timestamp)

def clear_session_start_time():
    return control_plane_service.clear_session_start_time(STATE)

def get_session_start_time(service_status=None):
    return control_plane_service.get_session_start_time(STATE, service_status)

def get_session_duration_text():
    return control_plane_service.get_session_duration_text(STATE)

def _log_source_settings(source):
    return minecraft_runtime_service.log_source_settings(STATE, source)

def get_log_source_text(source):
    return minecraft_runtime_service.get_log_source_text(STATE, source)

def ensure_log_stream_fetcher_started(source):
    return minecraft_runtime_service.ensure_log_stream_fetcher_started(STATE, source)

def _increment_log_stream_clients(source):
    return minecraft_runtime_service.increment_log_stream_clients(STATE, source)

def _decrement_log_stream_clients(source):
    return minecraft_runtime_service.decrement_log_stream_clients(STATE, source)

# ----------------------------
# Backup status and display helpers
# ----------------------------
def get_backups_status():
    return dashboard_runtime_service.get_backups_status(STATE)

def get_cpu_per_core_items(cpu_per_core):
    return dashboard_runtime_service.get_cpu_per_core_items(STATE, cpu_per_core)

def get_ram_usage_class(ram_usage):
    return dashboard_runtime_service.get_ram_usage_class(STATE, ram_usage)

def get_storage_usage_class(storage_usage):
    return dashboard_runtime_service.get_storage_usage_class(STATE, storage_usage)

def get_cpu_frequency_class(cpu_frequency):
    return dashboard_runtime_service.get_cpu_frequency_class(STATE, cpu_frequency)

def _refresh_rcon_config():
    return minecraft_runtime_service.refresh_rcon_config(STATE)

def is_rcon_enabled():
    return minecraft_runtime_service.is_rcon_enabled(STATE)


def _run_mcrcon(command, timeout=4):
    return minecraft_runtime_service.run_mcrcon(STATE, command, timeout=timeout)

def _probe_minecraft_runtime_metrics(force=False):
    return minecraft_runtime_service.probe_minecraft_runtime_metrics(STATE, force=force)

def get_players_online():
    return minecraft_runtime_service.get_players_online(STATE)

def get_tick_rate():
    return minecraft_runtime_service.get_tick_rate(STATE)
def get_service_status_display(service_status, players_online):
    return minecraft_runtime_service.get_service_status_display(STATE, service_status, players_online)

def get_service_status_class(service_status_display):
    return minecraft_runtime_service.get_service_status_class(service_status_display)

def graceful_stop_minecraft():
    return control_plane_service.graceful_stop_minecraft(STATE)

def stop_server_automatically():
    return control_plane_service.stop_server_automatically(STATE)

def run_backup_script(count_skip_as_success=True, trigger="manual"):
    return control_plane_service.run_backup_script(STATE, count_skip_as_success, trigger)

def format_backup_time(timestamp):
    return control_plane_service.format_backup_time(STATE, timestamp)

def get_server_time_text():
    return control_plane_service.get_server_time_text(STATE)

def get_latest_backup_zip_timestamp():
    return control_plane_service.get_latest_backup_zip_timestamp(STATE)

def get_backup_zip_snapshot():
    return control_plane_service.get_backup_zip_snapshot(STATE)

def backup_snapshot_changed(before_snapshot, after_snapshot):
    return control_plane_service.backup_snapshot_changed(STATE, before_snapshot, after_snapshot)

def get_backup_schedule_times(service_status=None):
    return control_plane_service.get_backup_schedule_times(STATE, service_status)

def get_backup_status():
    return control_plane_service.get_backup_status(STATE)

def is_backup_running():
    return control_plane_service.is_backup_running(STATE)

def reset_backup_schedule_state():
    return control_plane_service.reset_backup_schedule_state(STATE)

def collect_dashboard_metrics():
    return dashboard_runtime_service.collect_dashboard_metrics(STATE)

def _mark_home_page_client_active():
    return dashboard_runtime_service.mark_home_page_client_active(STATE)

def _collect_and_publish_metrics():
    return dashboard_runtime_service.collect_and_publish_metrics(STATE)

def metrics_collector_loop():
    return dashboard_runtime_service.metrics_collector_loop(STATE)

def ensure_metrics_collector_started():
    return dashboard_runtime_service.ensure_metrics_collector_started(STATE)

def get_cached_dashboard_metrics():
    return dashboard_runtime_service.get_cached_dashboard_metrics(STATE)

def format_countdown(seconds):
    return session_watchers_service.format_countdown(seconds)

def get_idle_countdown(service_status=None, players_online=None):
    return session_watchers_service.get_idle_countdown(STATE, service_status, players_online)

def idle_player_watcher():
    return session_watchers_service.idle_player_watcher(STATE)

def start_idle_player_watcher():
    return session_watchers_service.start_idle_player_watcher(STATE)

def backup_session_watcher():
    return session_watchers_service.backup_session_watcher(STATE)

def start_backup_session_watcher():
    return session_watchers_service.start_backup_session_watcher(STATE)

def initialize_session_tracking():
    return session_watchers_service.initialize_session_tracking(STATE)

def _status_debug_note():
    return session_watchers_service.status_debug_note(STATE)

def _session_write_failed_response():
    # Uniform response when session file cannot be written.
    message = "Session file write failed."
    if _is_ajax_request():
        return jsonify({"ok": False, "error": "session_write_failed", "message": f"{message} {_status_debug_note()}"}), 500
    return redirect("/?msg=session_write_failed")

def _ensure_csrf_token():
    # Return existing CSRF token from session or create a new one.
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

def _is_csrf_valid():
    # Validate request CSRF token using header (AJAX) or form field fallback.
    expected = session.get("csrf_token")
    if not expected:
        return False
    supplied = (
        request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or ""
    )
    return supplied == expected

def ensure_session_tracking_initialized():
    # Run session tracking initialization once per process.
    if session_state.initialized:
        return
    with session_state.init_lock:
        if session_state.initialized:
            return
        initialize_session_tracking()
        session_state.initialized = True

@app.before_request
def _initialize_session_tracking_before_request():
    # Ensure background state is initialized even under WSGI launch.
    ensure_session_tracking_initialized()
    ensure_metrics_collector_started()
    _ensure_csrf_token()
    csrf_exempt_paths = {"/home-heartbeat", "/file-page-heartbeat"}
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.path not in csrf_exempt_paths
        and not _is_csrf_valid()
    ):
        log_mcweb_action("reject", command=request.path, rejection_message="Security check failed (csrf_invalid).")
        return _csrf_rejected_response()

def _is_ajax_request():
    # Return True when request expects JSON response (fetch/XHR).
    # Primary AJAX signal used by fetch requests from this UI.
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    # Fallback signal when only Accept header is provided.
    accept = request.headers.get("Accept", "")
    return "application/json" in accept.lower()

def _ok_response():
    # Return appropriate success response for ajax/non-ajax requests.
    # AJAX callers need JSON, while legacy form submissions expect redirect.
    if _is_ajax_request():
        return jsonify({"ok": True})
    return redirect("/")

def _password_rejected_response():
    # Return password rejection response for ajax/non-ajax requests.
    # Keep one shared password-rejected payload/message for consistency.
    if _is_ajax_request():
        return jsonify({
            "ok": False,
            "error": "password_incorrect",
            "message": "Password incorrect. Whatever you were trying to do is cancelled.",
        }), 403
    return redirect("/?msg=password_incorrect")

def _backup_failed_response(message):
    # Return backup failure response for ajax/non-ajax requests.
    if _is_ajax_request():
        return jsonify({"ok": False, "error": "backup_failed", "message": message}), 500
    return redirect("/?msg=backup_failed")

def _csrf_rejected_response():
    # Return CSRF validation failure response for ajax/non-ajax requests.
    if _is_ajax_request():
        return jsonify({
            "ok": False,
            "error": "csrf_invalid",
            "message": "Security check failed. Please refresh and try again.",
        }), 403
    return redirect("/?msg=csrf_invalid")

def _rcon_rejected_response(message, status_code):
    # Return RCON validation/runtime failure for ajax/non-ajax requests.
    if _is_ajax_request():
        return jsonify({"ok": False, "message": message}), status_code
    return redirect("/")

@app.errorhandler(Exception)
def _unhandled_exception_handler(exc):
    # Log uncaught Flask request exceptions to mcweb action log.
    path = request.path if has_request_context() else "unknown-path"
    log_mcweb_exception(f"unhandled_exception path={path}", exc)
    if _is_ajax_request():
        return jsonify({"ok": False, "error": "internal_error", "message": "Internal server error."}), 500
    return redirect("/?msg=internal_error")

# ----------------------------
# Flask routes
# ----------------------------
STATE = AppState.from_namespace(globals())
register_routes(app, STATE)

def run_server():
    # Start background automation loops before serving HTTP requests.
    log_mcweb_boot_diagnostics()
    try:
        _load_minecraft_log_cache_from_journal()
        _load_mcweb_log_cache_from_disk()
        if not is_backup_running():
            _load_backup_log_cache_from_disk()
        ensure_session_tracking_initialized()
        ensure_metrics_collector_started()
        _collect_and_publish_metrics()
        start_idle_player_watcher()
        start_backup_session_watcher()
        app.run(
            host=_cfg_str("WEB_HOST", "0.0.0.0"),
            port=_cfg_int("WEB_PORT", 8080, minimum=1),
        )
    except Exception as exc:
        log_mcweb_exception("mcweb_main", exc)
        raise


if __name__ == "__main__":
    run_server()
