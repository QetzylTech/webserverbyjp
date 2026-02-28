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
import shutil
import json
import os
import secrets
import traceback
from collections import deque
from zoneinfo import ZoneInfo

app = Flask(__name__)
app.config["SECRET_KEY"] = (
    os.environ.get("MCWEB_SECRET_KEY")
    or os.environ.get("FLASK_SECRET_KEY")
    or secrets.token_hex(32)
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

# Core service and application settings.
FAVICON_URL = "https://static.wikia.nocookie.net/logopedia/images/e/e3/Minecraft_Launcher.svg/revision/latest/scale-to-width-down/250?cb=20230616222246"
SERVICE = "minecraft"
# BACKUP_SCRIPT = "/opt/Minecraft/webserverbyjp/backup.sh"
BACKUP_SCRIPT = Path(__file__).resolve().parent / "backup.sh"
BACKUP_DIR = Path("/home/marites/backups")
# CRASH_REPORTS_DIR = Path("/opt/Minecraft/crash-reports")
CRASH_REPORTS_DIR = Path(__file__).resolve().parent.parent / "crash-reports"
# MINECRAFT_LOGS_DIR = Path("/opt/Minecraft/logs")
MINECRAFT_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
BACKUP_LOG_FILE = Path(__file__).resolve().parent / "logs/backup.log"
MCWEB_LOG_DIR = Path(__file__).resolve().parent / "logs"
MCWEB_ACTION_LOG_FILE = MCWEB_LOG_DIR / "mcweb-actions.log"
# BACKUP_STATE_FILE = Path("/opt/Minecraft/webserverbyjp/state.txt")
BACKUP_STATE_FILE = Path(__file__).resolve().parent / "state.txt"
SESSION_FILE = Path(__file__).resolve().parent / "session.txt"
# "PST" here refers to Philippines Standard Time (UTC+8), not Pacific Time.
DISPLAY_TZ = ZoneInfo("Asia/Manila")
RCON_HOST = "127.0.0.1"
RCON_PORT = 25575
SERVER_PROPERTIES_CANDIDATES = [
    Path("/opt/Minecraft/server.properties"),
    Path("/opt/Minecraft/server/server.properties"),
    Path(__file__).resolve().parent / "server.properties",
    Path(__file__).resolve().parent.parent / "server.properties",
]

def _static_asset_version(filename):
    # Version token for static assets based on each file's mtime.
    try:
        path = Path(app.root_path) / "static" / filename
        return int(path.stat().st_mtime)
    except OSError:
        return 0

@app.context_processor
def inject_asset_helpers():
    # Expose per-file static version helper to templates.
    return {"static_version": _static_asset_version}

# Backup and automation timing controls.
BACKUP_INTERVAL_HOURS = 3
BACKUP_INTERVAL_SECONDS = max(60, int(BACKUP_INTERVAL_HOURS * 3600))
IDLE_ZERO_PLAYERS_SECONDS = 180
IDLE_CHECK_INTERVAL_SECONDS = 5

# Shared watcher state (protected by the locks below).
idle_zero_players_since = None
idle_lock = threading.Lock()
backup_periodic_runs = 0
backup_lock = threading.Lock()
backup_run_lock = threading.Lock()
backup_last_error = ""
session_tracking_initialized = False
session_tracking_lock = threading.Lock()
service_status_intent = None
service_status_intent_lock = threading.Lock()

OFF_STATES = {"inactive", "failed"}
LOG_SOURCE_KEYS = ("minecraft", "backup", "mcweb")

# Cache Minecraft runtime probes so rapid UI polling does not overwhelm RCON.
MC_QUERY_INTERVAL_SECONDS = 3
RCON_STARTUP_FALLBACK_AFTER_SECONDS = 120
RCON_STARTUP_FALLBACK_INTERVAL_SECONDS = 5
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
METRICS_COLLECT_INTERVAL_SECONDS = 1
METRICS_STREAM_HEARTBEAT_SECONDS = 5
LOG_STREAM_HEARTBEAT_SECONDS = 5
HOME_PAGE_ACTIVE_TTL_SECONDS = 30
HOME_PAGE_HEARTBEAT_INTERVAL_MS = 10000
FILE_PAGE_CACHE_REFRESH_SECONDS = 15
FILE_PAGE_ACTIVE_TTL_SECONDS = 30
FILE_PAGE_HEARTBEAT_INTERVAL_MS = 10000
LOG_STREAM_EVENT_BUFFER_SIZE = 800
CRASH_STOP_GRACE_SECONDS = 15
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
        "start_lock": threading.Lock(),
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
    result = subprocess.run(
        ["systemctl", "is-active", SERVICE],
        capture_output=True, text=True
    )
    return result.stdout.strip()

def _sanitize_log_fragment(text):
    # Flatten user/system text into one line for action logs.
    return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split()).strip()

def _format_file_size(num_bytes):
    # Human-readable size for listing panels.
    value = float(max(0, num_bytes or 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.1f} {units[idx]}"

def _list_download_files(base_dir, pattern):
    # Return file metadata sorted newest first.
    items = []
    if not base_dir.exists() or not base_dir.is_dir():
        return items

    for path in base_dir.glob(pattern):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        ts = stat.st_mtime
        items.append({
            "name": path.name,
            "mtime": ts,
            "modified": datetime.fromtimestamp(ts, tz=DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z"),
            "size_text": _format_file_size(stat.st_size),
        })

    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items

def _read_recent_file_lines(path, limit):
    # Return the last `limit` lines from a UTF-8 text file.
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    lines = text.splitlines()
    if len(lines) > limit:
        lines = lines[-limit:]
    return lines

def _safe_file_mtime_ns(path):
    # Return file mtime_ns or None when missing/unreadable.
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None

def _load_backup_log_cache_from_disk():
    # Refresh in-memory backup log cache from backup.log tail.
    global backup_log_cache_loaded
    global backup_log_cache_mtime_ns
    lines = _read_recent_file_lines(BACKUP_LOG_FILE, 200)
    mtime_ns = _safe_file_mtime_ns(BACKUP_LOG_FILE)
    with backup_log_cache_lock:
        backup_log_cache_lines.clear()
        backup_log_cache_lines.extend(lines)
        backup_log_cache_loaded = True
        backup_log_cache_mtime_ns = mtime_ns

def _append_backup_log_cache_line(line):
    # Append one streamed backup log line to the in-memory tail cache.
    global backup_log_cache_loaded
    global backup_log_cache_mtime_ns
    clean = (line or "").rstrip("\r\n")
    if not clean:
        return
    with backup_log_cache_lock:
        backup_log_cache_lines.append(clean)
        backup_log_cache_loaded = True
        backup_log_cache_mtime_ns = _safe_file_mtime_ns(BACKUP_LOG_FILE)

def _get_cached_backup_log_text():
    # Return cached backup log text, loading once from disk when needed.
    current_mtime_ns = _safe_file_mtime_ns(BACKUP_LOG_FILE)
    with backup_log_cache_lock:
        loaded = backup_log_cache_loaded
        cached_mtime_ns = backup_log_cache_mtime_ns
        if loaded and cached_mtime_ns == current_mtime_ns:
            return "\n".join(backup_log_cache_lines).strip() or "(no logs)"
    _load_backup_log_cache_from_disk()
    with backup_log_cache_lock:
        return "\n".join(backup_log_cache_lines).strip() or "(no logs)"

def _load_minecraft_log_cache_from_journal():
    # Refresh in-memory minecraft log cache from journal tail.
    global minecraft_log_cache_loaded
    result = subprocess.run(
        ["journalctl", "-u", SERVICE, "-n", "1000", "--no-pager"],
        capture_output=True,
        text=True,
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    lines = output.splitlines() if output else []
    with minecraft_log_cache_lock:
        minecraft_log_cache_lines.clear()
        minecraft_log_cache_lines.extend(lines)
        minecraft_log_cache_loaded = True

def _append_minecraft_log_cache_line(line):
    # Append one minecraft journal line to in-memory cache.
    global minecraft_log_cache_loaded
    clean = (line or "").rstrip("\r\n")
    if not clean:
        return
    with minecraft_log_cache_lock:
        minecraft_log_cache_lines.append(clean)
        minecraft_log_cache_loaded = True

def _get_cached_minecraft_log_text():
    # Return cached minecraft log text, loading once from journal when needed.
    with minecraft_log_cache_lock:
        if minecraft_log_cache_loaded:
            return "\n".join(minecraft_log_cache_lines).strip() or "(no logs)"
    _load_minecraft_log_cache_from_journal()
    with minecraft_log_cache_lock:
        return "\n".join(minecraft_log_cache_lines).strip() or "(no logs)"

def _load_mcweb_log_cache_from_disk():
    # Refresh in-memory mcweb action log cache from file tail.
    global mcweb_log_cache_loaded
    global mcweb_log_cache_mtime_ns
    lines = _read_recent_file_lines(MCWEB_ACTION_LOG_FILE, 200)
    mtime_ns = _safe_file_mtime_ns(MCWEB_ACTION_LOG_FILE)
    with mcweb_log_cache_lock:
        mcweb_log_cache_lines.clear()
        mcweb_log_cache_lines.extend(lines)
        mcweb_log_cache_loaded = True
        mcweb_log_cache_mtime_ns = mtime_ns

def _append_mcweb_log_cache_line(line):
    # Append one mcweb log line to in-memory cache.
    global mcweb_log_cache_loaded
    global mcweb_log_cache_mtime_ns
    clean = (line or "").rstrip("\r\n")
    if not clean:
        return
    with mcweb_log_cache_lock:
        mcweb_log_cache_lines.append(clean)
        mcweb_log_cache_loaded = True
        mcweb_log_cache_mtime_ns = _safe_file_mtime_ns(MCWEB_ACTION_LOG_FILE)

def _get_cached_mcweb_log_text():
    # Return cached mcweb log text, refreshing on file changes.
    current_mtime_ns = _safe_file_mtime_ns(MCWEB_ACTION_LOG_FILE)
    with mcweb_log_cache_lock:
        loaded = mcweb_log_cache_loaded
        cached_mtime_ns = mcweb_log_cache_mtime_ns
        if loaded and cached_mtime_ns == current_mtime_ns:
            return "\n".join(mcweb_log_cache_lines).strip() or "(no logs)"
    _load_mcweb_log_cache_from_disk()
    with mcweb_log_cache_lock:
        return "\n".join(mcweb_log_cache_lines).strip() or "(no logs)"

def _set_file_page_items(cache_key, items):
    # Replace cached page items with a fresh immutable snapshot.
    with file_page_cache_lock:
        file_page_cache[cache_key] = {
            "items": [dict(item) for item in items],
            "updated_at": time.time(),
        }

def _refresh_file_page_items(cache_key):
    # Refresh one file-list page cache entry.
    if cache_key == "backups":
        items = _list_download_files(BACKUP_DIR, "*.zip")
    elif cache_key == "crash_logs":
        items = _list_download_files(CRASH_REPORTS_DIR, "*.txt")
    elif cache_key == "minecraft_logs":
        items = _list_download_files(MINECRAFT_LOGS_DIR, "*.log")
        items.extend(_list_download_files(MINECRAFT_LOGS_DIR, "*.gz"))
        items.sort(key=lambda item: item["mtime"], reverse=True)
    else:
        return []
    _set_file_page_items(cache_key, items)
    return items

def _mark_file_page_client_active():
    # Mark that at least one file page client has pinged recently.
    global file_page_last_seen
    with file_page_cache_lock:
        file_page_last_seen = time.time()

def _has_active_file_page_clients():
    # Return True when file page clients have pinged recently.
    with file_page_cache_lock:
        last_seen = file_page_last_seen
    return (time.time() - last_seen) <= FILE_PAGE_ACTIVE_TTL_SECONDS

def get_cached_file_page_items(cache_key):
    # Return cached file list; refresh on-demand if stale/empty.
    with file_page_cache_lock:
        entry = file_page_cache.get(cache_key)
        if entry:
            age = time.time() - entry["updated_at"]
            if entry["items"] and age <= FILE_PAGE_CACHE_REFRESH_SECONDS:
                return [dict(item) for item in entry["items"]]
    return _refresh_file_page_items(cache_key)

def file_page_cache_refresher_loop():
    # Refresh file-list caches only while file page clients are active.
    while True:
        if _has_active_file_page_clients():
            for cache_key in ("backups", "crash_logs", "minecraft_logs"):
                try:
                    _refresh_file_page_items(cache_key)
                except Exception as exc:
                    log_mcweb_exception(f"file_page_cache_refresh/{cache_key}", exc)
            time.sleep(FILE_PAGE_CACHE_REFRESH_SECONDS)
        else:
            time.sleep(1)

def ensure_file_page_cache_refresher_started():
    # Start file-page cache refresher exactly once.
    global file_page_cache_refresher_started
    if file_page_cache_refresher_started:
        return
    with file_page_cache_refresher_start_lock:
        if file_page_cache_refresher_started:
            return
        watcher = threading.Thread(target=file_page_cache_refresher_loop, daemon=True)
        watcher.start()
        file_page_cache_refresher_started = True

def _safe_filename_in_dir(base_dir, filename):
    # Ensure requested file is a direct child file of base_dir.
    if not filename:
        return None
    name = Path(filename).name
    if name != filename:
        return None
    candidate = (base_dir / name)
    try:
        base_resolved = base_dir.resolve()
        candidate_resolved = candidate.resolve()
    except OSError:
        return None
    try:
        candidate_resolved.relative_to(base_resolved)
    except ValueError:
        return None
    if not candidate_resolved.exists() or not candidate_resolved.is_file():
        return None
    return name

def _get_client_ip():
    # Prefer reverse-proxy headers, then direct client address.
    if not has_request_context():
        return "mcweb"
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    x_real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if x_real_ip:
        return x_real_ip
    direct = (request.remote_addr or "").strip()
    return direct or "mcweb"

def log_mcweb_action(action, command=None, rejection_message=None):
    # Append one mcweb action line:
    # Mon dd HH:MM:SS <client-ip> [mcweb/action] command? rejection?
    timestamp = datetime.now(tz=DISPLAY_TZ).strftime("%b %d %H:%M:%S")
    client_ip = _sanitize_log_fragment(_get_client_ip()) or "unknown"
    safe_action = _sanitize_log_fragment(action) or "unknown"
    parts = [f"{timestamp} <{client_ip}> [mcweb/{safe_action}]"]
    if command:
        safe_command = _sanitize_log_fragment(command)
        if safe_command:
            parts.append(safe_command)
    if rejection_message:
        safe_rejection = _sanitize_log_fragment(rejection_message)
        if safe_rejection:
            parts.append(f"rejected: {safe_rejection}")
    line = " ".join(parts).strip()
    if not line:
        return
    try:
        MCWEB_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with MCWEB_ACTION_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        # Logging must not break control endpoints.
        pass

def log_mcweb_exception(context, exc):
    # Record exception class/message and traceback summary in action log.
    exc_name = type(exc).__name__ if exc is not None else "Exception"
    exc_text = _sanitize_log_fragment(str(exc) if exc is not None else "")
    tb = ""
    if exc is not None:
        tb = _sanitize_log_fragment(" | ".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    message = f"{context}: {exc_name}"
    if exc_text:
        message += f": {exc_text}"
    if tb:
        # Keep log lines bounded.
        message += f" | traceback: {tb[:700]}"
    log_mcweb_action("error", rejection_message=message)

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
    # Set transient UI status intent: 'starting', 'shutting', 'crashed', or None.
    global service_status_intent
    with service_status_intent_lock:
        service_status_intent = intent

def get_service_status_intent():
    # Read transient UI status intent.
    with service_status_intent_lock:
        return service_status_intent

def stop_service_systemd():
    # Attempt to stop the service and verify it is no longer active.
    # Use only configured sudo-backed command to avoid interactive PolicyKit prompts.
    try:
        run_sudo(["systemctl", "stop", SERVICE])
    except Exception as exc:
        log_mcweb_exception("stop_service_systemd", exc)

    # Give systemd a short window to transition to inactive/failed.
    deadline = time.time() + 10
    while time.time() < deadline:
        if get_status() in OFF_STATES:
            return True
        time.sleep(0.5)
    return False

def get_sudo_password():
    # Return sudo password, sourced from rcon.password in server.properties.
    password, _, enabled = _refresh_rcon_config()
    if not enabled or not password:
        return None
    return password


def run_sudo(cmd):
    # Run a command with sudo using the password sourced from server.properties.
    sudo_password = get_sudo_password()
    if not sudo_password:
        raise RuntimeError("sudo password unavailable: rcon.password not found in server.properties")

    result = subprocess.run(
        ["sudo", "-S"] + cmd,
        input=f"{sudo_password}\n",
        capture_output=True,
        text=True,
    )
    return result


def validate_sudo_password(sudo_password):
    # Validate user-supplied password against rcon.password from server.properties.
    expected_password = get_sudo_password()
    if not expected_password:
        return False
    return (sudo_password or "").strip() == expected_password

def ensure_session_file():
    # Ensure the session timestamp file exists.
    try:
        SESSION_FILE.touch(exist_ok=True)
        return True
    except OSError:
        return False

def read_session_start_time():
    # Read session start UNIX timestamp from session file, or None.
    if not ensure_session_file():
        return None
    try:
        raw = SESSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        ts = float(raw)
    except ValueError:
        return None
    if ts <= 0:
        return None
    # Accept accidental millisecond epoch values.
    if ts > 1_000_000_000_000:
        ts = ts / 1000.0
    return ts

def write_session_start_time(timestamp=None):
    # Persist session start UNIX timestamp to session file.
    if not ensure_session_file():
        return None
    ts = time.time() if timestamp is None else float(timestamp)
    try:
        SESSION_FILE.write_text(f"{ts:.6f}\n", encoding="utf-8")
    except OSError:
        return None
    return ts

def clear_session_start_time():
    # Clear persisted session start timestamp.
    if not ensure_session_file():
        return False
    try:
        SESSION_FILE.write_text("", encoding="utf-8")
    except OSError:
        return False
    return True

def get_session_start_time(service_status=None):
    # Return session start time from session.txt when service is not off.
    if service_status is None:
        service_status = get_status()

    if service_status in OFF_STATES:
        return None
    return read_session_start_time()

def get_session_duration_text():
    # Return elapsed session duration based strictly on session.txt UNIX time.
    start_time = read_session_start_time()
    if start_time is None:
        return "--"
    # If clock/timestamp is slightly ahead, clamp to zero instead of hiding duration.
    elapsed = max(0, int(time.time() - start_time))
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def _log_source_settings(source):
    normalized = (source or "").strip().lower()
    if normalized not in LOG_SOURCE_KEYS:
        return None
    if normalized == "minecraft":
        return {
            "type": "journal",
            "context": "minecraft_log_stream",
            "unit": SERVICE,
            "text_limit": 1000,
        }
    if normalized == "backup":
        return {
            "type": "file",
            "context": "backup_log_stream",
            "path": BACKUP_LOG_FILE,
            "text_limit": 200,
        }
    return {
        "type": "file",
        "context": "mcweb_action_log_stream",
        "path": MCWEB_ACTION_LOG_FILE,
        "text_limit": 200,
    }

def get_log_source_text(source):
    # Return recent logs for the requested source.
    settings = _log_source_settings(source)
    if settings is None:
        return None

    if settings["type"] == "journal":
        if source == "minecraft":
            return _get_cached_minecraft_log_text()
        result = subprocess.run(
            ["journalctl", "-u", settings["unit"], "-n", str(settings["text_limit"]), "--no-pager"],
            capture_output=True,
            text=True,
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        return output or "(no logs)"

    path = settings["path"]
    if source == "backup":
        # When backup is idle, serve preloaded in-memory backup log tail.
        if not is_backup_running():
            return _get_cached_backup_log_text()
        # During active backup, read the latest tail from disk.
        lines = _read_recent_file_lines(path, settings["text_limit"])
        text = "\n".join(lines).strip() or "(no logs)"
        _load_backup_log_cache_from_disk()
        return text
    if source == "mcweb":
        return _get_cached_mcweb_log_text()

    lines = _read_recent_file_lines(path, settings["text_limit"])
    return "\n".join(lines).strip() or "(no logs)"

def _publish_log_stream_line(source, line):
    # Publish one log line event for all subscribers of a source stream.
    state = log_stream_states.get(source)
    if state is None:
        return
    with state["cond"]:
        state["seq"] += 1
        state["events"].append((state["seq"], line))
        state["cond"].notify_all()
    if source == "backup":
        _append_backup_log_cache_line(line)
    elif source == "minecraft":
        _append_minecraft_log_cache_line(line)
    elif source == "mcweb":
        _append_mcweb_log_cache_line(line)

def _line_matches_crash_marker(line):
    text = (line or "").lower()
    return any(marker.lower() in text for marker in CRASH_STOP_MARKERS)

def _crash_stop_after_grace(trigger_line):
    # Wait for crash grace period, then stop through systemd if still active.
    global crash_stop_timer_active
    try:
        time.sleep(CRASH_STOP_GRACE_SECONDS)
        status = get_status()
        if status == "active":
            stopped = stop_service_systemd()
            if stopped:
                log_mcweb_action(
                    "auto-stop-crash",
                    command=f"marker={trigger_line} grace={CRASH_STOP_GRACE_SECONDS}s",
                )
            else:
                log_mcweb_action(
                    "auto-stop-crash",
                    command=f"marker={trigger_line} grace={CRASH_STOP_GRACE_SECONDS}s",
                    rejection_message="systemd stop did not reach inactive/failed within timeout.",
                )
    finally:
        with crash_stop_lock:
            crash_stop_timer_active = False

def _schedule_crash_stop_if_needed(line):
    # Start at most one crash-stop timer while awaiting shutdown.
    global crash_stop_timer_active
    if not _line_matches_crash_marker(line):
        return
    set_service_status_intent("crashed")
    with crash_stop_lock:
        if crash_stop_timer_active:
            return
        crash_stop_timer_active = True
    worker = threading.Thread(target=_crash_stop_after_grace, args=(line,), daemon=True)
    worker.start()

def _log_source_fetcher_loop(source):
    # Background source reader: one subprocess per source, shared by all clients.
    settings = _log_source_settings(source)
    if settings is None:
        return

    while True:
        proc = None
        try:
            if settings["type"] == "journal":
                cmd = ["journalctl", "-u", settings["unit"], "-f", "-n", "0", "--no-pager"]
            else:
                path = settings["path"]
                if not path.exists():
                    time.sleep(1)
                    continue
                cmd = ["tail", "-n", "0", "-F", str(path)]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            if not proc.stdout:
                time.sleep(1)
                continue

            for line in proc.stdout:
                clean = line.rstrip("\r\n")
                if not clean:
                    continue
                _publish_log_stream_line(source, clean)
                if source == "minecraft":
                    _schedule_crash_stop_if_needed(clean)
        except Exception as exc:
            log_mcweb_exception(settings["context"], exc)
        finally:
            if proc and proc.poll() is None:
                proc.terminate()

        # Keep the fetcher alive if source command exits unexpectedly.
        time.sleep(1)

def ensure_log_stream_fetcher_started(source):
    # Start one background log fetcher per source.
    state = log_stream_states.get(source)
    if state is None:
        return
    if state["started"]:
        return
    with state["start_lock"]:
        if state["started"]:
            return
        watcher = threading.Thread(target=_log_source_fetcher_loop, args=(source,), daemon=True)
        watcher.start()
        state["started"] = True

def _is_rcon_startup_ready(service_status=None):
    # Return True once startup log confirms Minecraft is fully loaded.
    global rcon_startup_ready
    if service_status is None:
        service_status = get_status()

    if service_status != "active":
        with rcon_startup_lock:
            rcon_startup_ready = False
        return False

    with rcon_startup_lock:
        if rcon_startup_ready:
            return True

    result = subprocess.run(
        ["journalctl", "-u", SERVICE, "-n", "500", "--no-pager"],
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    ready = bool(RCON_STARTUP_READY_PATTERN.search(output))
    if ready:
        with rcon_startup_lock:
            rcon_startup_ready = True
    return ready

# ----------------------------
# Backup status and display helpers
# ----------------------------
def get_backups_status():
    # Return whether the backup directory is present and file count.
    if not BACKUP_DIR.exists() or not BACKUP_DIR.is_dir():
        return "missing"
    zip_count = sum(1 for _ in BACKUP_DIR.glob("*.zip"))
    return f"ready ({zip_count} zip files)"

def _read_proc_stat():
    # Read CPU stat lines from /proc/stat.
    with open("/proc/stat", "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.startswith("cpu")]

def _parse_cpu_times(line):
    # Parse total/idle jiffies from one /proc/stat CPU line.
    parts = line.split()
    values = [int(v) for v in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total, idle

def get_cpu_usage_per_core():
    # Compute per-core CPU usage by sampling /proc/stat twice.
    first = _read_proc_stat()
    time.sleep(0.15)
    second = _read_proc_stat()

    usages = []
    for i in range(1, min(len(first), len(second))):
        total1, idle1 = _parse_cpu_times(first[i])
        total2, idle2 = _parse_cpu_times(second[i])
        total_delta = total2 - total1
        idle_delta = idle2 - idle1
        if total_delta <= 0:
            usages.append("0.0")
            continue
        usage = 100.0 * (1.0 - (idle_delta / total_delta))
        usages.append(f"{usage:.1f}")
    return usages

def _class_from_percent(value):
    # Map percentage to severity color class for the dashboard.
    if value < 60:
        return "stat-green"
    if value < 75:
        return "stat-yellow"
    if value < 90:
        return "stat-orange"
    return "stat-red"

def _extract_percent(usage_text):
    # Extract percent value from strings like '12 / 100 (12.0%)'.
    match = re.search(r"\(([\d.]+)%\)", usage_text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None

def _usage_class_from_text(usage_text):
    # Color class for usage strings that include a '(NN.N%)' token.
    percent = _extract_percent(usage_text)
    if percent is None:
        return "stat-red"
    return _class_from_percent(percent)

def get_cpu_per_core_items(cpu_per_core):
    # Return per-core values with independent color classes.
    # Each core is rendered independently so one hot core does not hide others.
    items = []
    for i, raw in enumerate(cpu_per_core):
        try:
            val = float(raw)
        except ValueError:
            items.append({"index": i, "value": raw, "class": "stat-red"})
            continue
        items.append({"index": i, "value": f"{val:.1f}", "class": _class_from_percent(val)})
    return items

def get_ram_usage_class(ram_usage):
    # Color class based on RAM utilization percentage.
    return _usage_class_from_text(ram_usage)

def get_storage_usage_class(storage_usage):
    # Color class based on root filesystem utilization percentage.
    return _usage_class_from_text(storage_usage)

def get_cpu_frequency_class(cpu_frequency):
    # Color class for CPU frequency readout.
    return "stat-red" if cpu_frequency == "unknown" else "stat-green"

def get_ram_usage():
    # Return RAM usage string based on /proc/meminfo.
    mem_total_kb = 0
    mem_available_kb = 0
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                mem_total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available_kb = int(line.split()[1])

    if mem_total_kb <= 0:
        return "unknown"

    used_kb = mem_total_kb - mem_available_kb
    used_gb = used_kb / (1024 * 1024)
    total_gb = mem_total_kb / (1024 * 1024)
    percent = (used_kb / mem_total_kb) * 100.0
    return f"{used_gb:.2f} / {total_gb:.2f} GB ({percent:.1f}%)"

def get_cpu_frequency():
    # Return average current CPU frequency across cores.
    freq_paths = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq"))
    freqs_khz = []
    for path in freq_paths:
        try:
            value = path.read_text(encoding="utf-8").strip()
            freqs_khz.append(int(value))
        except (ValueError, OSError):
            continue

    if freqs_khz:
        avg_ghz = (sum(freqs_khz) / len(freqs_khz)) / 1_000_000
        return f"{avg_ghz:.2f} GHz"

    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            mhz_values = []
            for line in f:
                if line.lower().startswith("cpu mhz"):
                    mhz_values.append(float(line.split(":", 1)[1].strip()))
        if mhz_values:
            avg_ghz = (sum(mhz_values) / len(mhz_values)) / 1000
            return f"{avg_ghz:.2f} GHz"
    except OSError:
        pass

    return "unknown"

def get_storage_usage():
    # Return root filesystem usage from df -h.
    result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
    if result.returncode != 0:
        return "unknown"

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return "unknown"

    parts = lines[1].split()
    if len(parts) < 6:
        return "unknown"

    used = parts[2]
    size = parts[1]
    percent = parts[4]
    return f"{used} / {size} ({percent})"

def _candidate_mcrcon_bins():
    # Return possible mcrcon executable paths.
    candidates = []
    found = shutil.which("mcrcon")
    if found:
        candidates.append(found)
    for path in ("/usr/bin/mcrcon", "/usr/local/bin/mcrcon", "/opt/mcrcon/mcrcon"):
        if path not in candidates:
            candidates.append(path)
    return candidates

def _clean_rcon_output(text):
    # Normalize RCON output by removing color/control codes.
    cleaned = text or ""
    # Strip ANSI escape sequences.
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", cleaned)
    # Strip Minecraft section formatting codes.
    cleaned = re.sub(r"\u00a7.", "", cleaned)
    return cleaned

def _refresh_rcon_config():
    # Refresh RCON password/port from server.properties.
    #
    #     RCON is considered enabled only when rcon.password is present and non-empty.
    #     
    global rcon_cached_password
    global rcon_cached_port
    global rcon_cached_enabled
    global rcon_last_config_read_at

    now = time.time()
    with rcon_config_lock:
        # Refresh at most once per minute.
        if now - rcon_last_config_read_at < 60:
            return rcon_cached_password, rcon_cached_port, rcon_cached_enabled

        rcon_last_config_read_at = now
        parsed_password = None
        parsed_port = None

        for path in SERVER_PROPERTIES_CANDIDATES:
            if not path.exists():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue

            kv = {}
            for raw in lines:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                kv[key.strip()] = value.strip()

            if kv.get("enable-rcon", "").lower() == "false":
                continue

            candidate_password = kv.get("rcon.password", "").strip()
            if not candidate_password:
                continue

            parsed_password = candidate_password
            if kv.get("rcon.port", "").isdigit():
                parsed_port = int(kv.get("rcon.port"))
            break

        if parsed_password:
            rcon_cached_password = parsed_password
            rcon_cached_enabled = True
            if parsed_port:
                rcon_cached_port = parsed_port
        else:
            rcon_cached_password = None
            rcon_cached_enabled = False

        return rcon_cached_password, rcon_cached_port, rcon_cached_enabled

def is_rcon_enabled():
    # Return True when RCON credentials are available from server.properties.
    _, _, enabled = _refresh_rcon_config()
    return enabled


def _run_mcrcon(command, timeout=4):
    # Run one RCON command against local server (with compatibility fallbacks).
    password, port, enabled = _refresh_rcon_config()
    if not enabled or not password:
        raise RuntimeError("RCON is disabled: rcon.password not found in server.properties")

    last_result = None
    for bin_path in _candidate_mcrcon_bins():
        candidates = [
            [bin_path, "-H", RCON_HOST, "-P", str(port), "-p", password, command],
            [bin_path, "-H", RCON_HOST, "-p", password, command],
            [bin_path, "-p", password, command],
        ]
        for argv in candidates:
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                last_result = result
                if result.returncode == 0:
                    return result
            except Exception as exc:
                log_mcweb_exception("_run_mcrcon_candidate", exc)
                continue

    if last_result is not None:
        return last_result
    raise RuntimeError("mcrcon invocation failed")

def _parse_players_online(output):
    # Parse player count from common `list` output variants.
    text = _clean_rcon_output(output).strip()
    if not text:
        return None

    # Vanilla/Paper format: "There are N of a max of M players online".
    match = re.search(r"There are\s+(\d+)\s+of a max of", text, re.IGNORECASE)
    if match:
        return match.group(1)

    # Some servers return explicit no-player sentence.
    if re.search(r"\bno players online\b", text, re.IGNORECASE):
        return "0"

    # Generic fallback around "players online" phrase.
    match = re.search(r"(\d+)\s+players?\s+online", text, re.IGNORECASE)
    if match:
        return match.group(1)

    # "Players online: N"
    match = re.search(r"Players?\s+online:\s*(\d+)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    return None

def _probe_tick_rate():
    # Probe tick time using multiple command variants and return '<ms> ms' or None.
    try:
        result = _run_mcrcon("forge tps", timeout=8)
    except Exception as exc:
        log_mcweb_exception("_probe_tick_rate", exc)
        return None

    if result.returncode != 0:
        return None

    output = _clean_rcon_output((result.stdout or "") + (result.stderr or "")).strip()
    if not output:
        return None
    cleaned = output

    # Prefer direct mspt/ms values when available.
    ms_match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*ms", cleaned, re.IGNORECASE)
    if ms_match:
        try:
            ms_val = float(ms_match.group(1).replace(",", "."))
            if ms_val > 0:
                return f"{ms_val:.1f} ms"
        except ValueError:
            pass

    # Convert explicit TPS values to ms/tick.
    match = re.search(r"TPS[^0-9]*([0-9]+(?:[.,][0-9]+)?)", cleaned, re.IGNORECASE)
    if match:
        try:
            tps = float(match.group(1).replace(",", "."))
            if tps > 0:
                return f"{(1000.0 / tps):.1f} ms"
        except ValueError:
            pass

    # Fallback: parse first numeric token (guarded to plausible TPS range).
    match = re.search(r"\b([0-9]+(?:[.,][0-9]+)?)\b", cleaned)
    if match:
        try:
            tps = float(match.group(1).replace(",", "."))
            if 0 < tps <= 30:
                return f"{(1000.0 / tps):.1f} ms"
        except ValueError:
            pass

    return None

def _probe_minecraft_runtime_metrics(force=False):
    # Return cached/updated (players_online, tick_rate) values.
    global mc_last_query_at
    global mc_cached_players_online
    global mc_cached_tick_rate
    global rcon_startup_ready

    service_status = get_status()

    # Fast path: skip probing when the service is down.
    if service_status != "active":
        with mc_query_lock:
            mc_cached_players_online = "unknown"
            mc_cached_tick_rate = "unknown"
        return "unknown", "unknown"

    now = time.time()
    startup_ready = _is_rcon_startup_ready(service_status)
    use_startup_fallback_probe = False

    # Gate RCON queries until startup log confirms the world finished loading.
    # Fallback: after prolonged startup, probe at low cadence to recover when
    # log readiness line is missing/late.
    if not startup_ready:
        session_started_at = get_session_start_time(service_status)
        startup_elapsed = None
        if session_started_at is not None:
            startup_elapsed = max(0.0, now - session_started_at)
        if startup_elapsed is not None and startup_elapsed >= RCON_STARTUP_FALLBACK_AFTER_SECONDS:
            use_startup_fallback_probe = True
        else:
            with mc_query_lock:
                mc_cached_players_online = "unknown"
                mc_cached_tick_rate = "unknown"
            return "unknown", "unknown"

    with mc_query_lock:
        probe_interval = MC_QUERY_INTERVAL_SECONDS
        if use_startup_fallback_probe:
            probe_interval = max(MC_QUERY_INTERVAL_SECONDS, RCON_STARTUP_FALLBACK_INTERVAL_SECONDS)
        if not force and (now - mc_last_query_at) < probe_interval:
            return mc_cached_players_online, mc_cached_tick_rate

    players_value = None
    tick_value = None
    list_probe_ok = False

    try:
        result = _run_mcrcon("list", timeout=8)
        if result.returncode == 0:
            list_probe_ok = True
            combined = (result.stdout or "") + (result.stderr or "")
            players_value = _parse_players_online(combined)
    except Exception as exc:
        log_mcweb_exception("_probe_players_online", exc)

    try:
        tick_value = _probe_tick_rate()
    except Exception as exc:
        log_mcweb_exception("_probe_tick_wrapper", exc)

    # Promote to startup-ready once fallback probing confirms RCON responsiveness.
    if use_startup_fallback_probe and (list_probe_ok or tick_value is not None):
        with rcon_startup_lock:
            rcon_startup_ready = True

    with mc_query_lock:
        # Keep last known values on transient RCON failures while service is active.
        if players_value is not None:
            mc_cached_players_online = players_value

        if tick_value is not None:
            mc_cached_tick_rate = tick_value

        mc_last_query_at = now
        return mc_cached_players_online, mc_cached_tick_rate

def get_players_online():
    # Return online player count from cached RCON probe.
    players_online, _ = _probe_minecraft_runtime_metrics()
    return players_online

def get_tick_rate():
    # Return server tick time from cached RCON probe.
    _, tick_rate = _probe_minecraft_runtime_metrics()
    return tick_rate
def get_service_status_display(service_status, players_online):
    # Map raw service + start/stop intent into rule-based UI status labels.
    intent = get_service_status_intent()

    # Crash marker detection has highest priority until a new lifecycle action updates intent.
    if intent == "crashed":
        return "Crashed"

    # Rule 1: show Off when systemd says the service is off.
    if service_status in ("inactive", "failed"):
        set_service_status_intent(None)
        return "Off"

    # Transitional systemd states keep clear lifecycle labels.
    if service_status == "activating":
        return "Starting"
    if service_status == "deactivating":
        return "Shutting Down"

    # Active state: apply intent rules based on players and transient UI intent.
    if service_status == "active":
        players_is_integer = isinstance(players_online, str) and players_online.isdigit()

        # Rule 2: show Running when systemd is active and players is an integer.
        if players_is_integer:
            # Once players become resolvable, startup/shutdown transient intent is done.
            if intent in ("starting", "shutting"):
                set_service_status_intent(None)
            return "Running"

        # Rules 3 and 4: handle unknown player count with trigger intent.
        if intent == "shutting":
            return "Shutting Down"
        # Default unknown-on-active and explicit start intent both map to Starting.
        return "Starting"

    return "Off"

def get_service_status_class(service_status_display):
    # Map display status to UI severity color class.
    if service_status_display == "Running":
        return "stat-green"
    if service_status_display == "Starting":
        return "stat-yellow"
    if service_status_display == "Shutting Down":
        return "stat-orange"
    if service_status_display == "Crashed":
        return "stat-red"
    return "stat-red"

def graceful_stop_minecraft():
    # Stop sequence: systemd stop -> backup.
    # Run steps in strict order, regardless of intermediate failures.
    systemd_ok = stop_service_systemd()
    backup_ok = run_backup_script()
    return {
        "systemd_ok": systemd_ok,
        "backup_ok": backup_ok,
    }

def stop_server_automatically():
    # Gracefully stop Minecraft (used by idle watcher).
    set_service_status_intent("shutting")
    graceful_stop_minecraft()
    clear_session_start_time()
    reset_backup_schedule_state()

def run_backup_script(count_skip_as_success=True):
    # Run backup script and update in-memory backup status.
    global backup_last_error

    # Prevent duplicate launches from concurrent triggers in this process.
    if not backup_run_lock.acquire(blocking=False):
        return bool(count_skip_as_success)
    try:
        # Honor backup.sh state lock so overlapping runs are skipped.
        if is_backup_running():
            with backup_lock:
                backup_last_error = ""
            return bool(count_skip_as_success)

        with backup_lock:
            backup_last_error = ""

        before_snapshot = get_backup_zip_snapshot()
        # Try direct execution first; some setups succeed even if script emits
        # non-zero due to auxiliary commands (e.g., mcrcon syntax mismatch).
        direct_result = subprocess.run(
            [BACKUP_SCRIPT],
            capture_output=True,
            text=True,
            timeout=600,
        )
        after_direct_snapshot = get_backup_zip_snapshot()
        direct_created_zip = backup_snapshot_changed(before_snapshot, after_direct_snapshot)

        if direct_result.returncode == 0 or direct_created_zip:
            return True
        else:
            err = (
                (direct_result.stderr or "")
                + "\n"
                + (direct_result.stdout or "")
            ).strip()
            with backup_lock:
                backup_last_error = err[:700] if err else "Backup command returned non-zero exit status."
            return False
    finally:
        backup_run_lock.release()

def format_backup_time(timestamp):
    # Format UNIX timestamp for the dashboard or return '--'.
    if timestamp is None:
        return "--"
    return datetime.fromtimestamp(timestamp, tz=DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z")

def get_server_time_text():
    # Return current server time for header display.
    return datetime.now(tz=DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z")

def get_latest_backup_zip_timestamp():
    # Return mtime of newest ZIP backup file, if available.
    if not BACKUP_DIR.exists() or not BACKUP_DIR.is_dir():
        return None
    latest = None
    for path in BACKUP_DIR.glob("*.zip"):
        try:
            ts = path.stat().st_mtime
        except OSError:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest

def get_backup_zip_snapshot():
    # Return snapshot of zip files as {path: mtime_ns} for change detection.
    snapshot = {}
    if not BACKUP_DIR.exists() or not BACKUP_DIR.is_dir():
        return snapshot
    for path in BACKUP_DIR.glob("*.zip"):
        try:
            snapshot[str(path)] = path.stat().st_mtime_ns
        except OSError:
            continue
    return snapshot

def backup_snapshot_changed(before_snapshot, after_snapshot):
    # Return True when backup artifacts changed (new file or updated mtime).
    if not before_snapshot and after_snapshot:
        return True
    for file_path, after_mtime in after_snapshot.items():
        before_mtime = before_snapshot.get(file_path)
        if before_mtime is None:
            return True
        if after_mtime != before_mtime:
            return True
    return False

def get_backup_schedule_times(service_status=None):
    # Return last/next backup timestamps for dashboard display.
    if service_status is None:
        service_status = get_status()

    # Last backup is strictly the newest ZIP found in backup folder.
    latest_zip_ts = get_latest_backup_zip_timestamp()
    last_backup_ts = latest_zip_ts

    next_backup_at = None
    if service_status not in OFF_STATES:
        # Fixed periodic schedule anchored to session start.
        session_start = get_session_start_time(service_status)
        if session_start is not None:
            elapsed_intervals = int(max(0, time.time() - session_start) // BACKUP_INTERVAL_SECONDS)
            next_backup_at = session_start + ((elapsed_intervals + 1) * BACKUP_INTERVAL_SECONDS)

    return {
        "last_backup_time": format_backup_time(last_backup_ts),
        "next_backup_time": format_backup_time(next_backup_at),
    }

def get_backup_status():
    # Return backup status from backup state file: true=Running, false=Idle.
    if is_backup_running():
        return "Running", "stat-green"
    return "Idle", "stat-yellow"

def is_backup_running():
    # Return True when backup state file indicates an active backup run.
    try:
        raw = BACKUP_STATE_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return False
    return raw == "true"

def reset_backup_schedule_state():
    # Reset periodic backup schedule state for a new/ended session.
    global backup_periodic_runs
    with backup_lock:
        backup_periodic_runs = 0

def collect_dashboard_metrics():
    # Collect shared dashboard metrics for both HTML and JSON responses.
    cpu_per_core = get_cpu_usage_per_core()
    ram_usage = get_ram_usage()
    cpu_frequency = get_cpu_frequency()
    storage_usage = get_storage_usage()
    service_status = get_status()
    players_online = get_players_online()
    tick_rate = get_tick_rate()
    session_duration = get_session_duration_text()
    service_status_display = get_service_status_display(service_status, players_online)
    backup_schedule = get_backup_schedule_times(service_status)
    backup_status, backup_status_class = get_backup_status()

    return {
        "service_status": service_status_display,
        "service_status_class": get_service_status_class(service_status_display),
        "service_running_status": service_status,
        "backups_status": get_backups_status(),
        "ram_usage": ram_usage,
        "ram_usage_class": get_ram_usage_class(ram_usage),
        "cpu_per_core_items": get_cpu_per_core_items(cpu_per_core),
        "cpu_frequency": cpu_frequency,
        "cpu_frequency_class": get_cpu_frequency_class(cpu_frequency),
        "storage_usage": storage_usage,
        "storage_usage_class": get_storage_usage_class(storage_usage),
        "players_online": players_online,
        "tick_rate": tick_rate,
        "session_duration": session_duration,
        "idle_countdown": get_idle_countdown(service_status, players_online),
        "backup_status": backup_status,
        "backup_status_class": backup_status_class,
        "last_backup_time": backup_schedule["last_backup_time"],
        "next_backup_time": backup_schedule["next_backup_time"],
        "server_time": get_server_time_text(),
        "rcon_enabled": is_rcon_enabled(),
    }

def _publish_metrics_snapshot(snapshot):
    # Publish one metrics snapshot to the shared cache and notify stream listeners.
    global metrics_cache_payload
    global metrics_cache_seq
    with metrics_cache_cond:
        metrics_cache_payload = snapshot
        metrics_cache_seq += 1
        metrics_cache_cond.notify_all()

def _mark_home_page_client_active():
    # Mark that at least one dashboard client has pinged recently.
    global home_page_last_seen
    with metrics_cache_cond:
        home_page_last_seen = time.time()
        metrics_cache_cond.notify_all()

def _has_active_home_page_clients():
    # Return True when dashboard clients pinged recently.
    with metrics_cache_cond:
        last_seen = home_page_last_seen
    return (time.time() - last_seen) <= HOME_PAGE_ACTIVE_TTL_SECONDS

def _collect_and_publish_metrics():
    # Collect dashboard metrics once and publish; return success flag.
    try:
        snapshot = collect_dashboard_metrics()
    except Exception as exc:
        log_mcweb_exception("metrics_collect", exc)
        return False
    _publish_metrics_snapshot(snapshot)
    return True

def metrics_collector_loop():
    # Background loop: collect shared dashboard metrics only while dashboard clients are active.
    while True:
        with metrics_cache_cond:
            metrics_cache_cond.wait_for(
                lambda: metrics_stream_client_count > 0 or _has_active_home_page_clients(),
                timeout=1,
            )
            should_collect = metrics_stream_client_count > 0 or _has_active_home_page_clients()
        if not should_collect:
            continue
        _collect_and_publish_metrics()
        with metrics_cache_cond:
            if metrics_stream_client_count > 0 or _has_active_home_page_clients():
                metrics_cache_cond.wait(timeout=METRICS_COLLECT_INTERVAL_SECONDS)

def ensure_metrics_collector_started():
    # Start metrics collector exactly once per process.
    global metrics_collector_started
    if metrics_collector_started:
        return
    with metrics_collector_start_lock:
        if metrics_collector_started:
            return
        watcher = threading.Thread(target=metrics_collector_loop, daemon=True)
        watcher.start()
        metrics_collector_started = True

def get_cached_dashboard_metrics():
    # Return latest shared metrics snapshot, or defaults when cache is cold.
    with metrics_cache_cond:
        if metrics_cache_payload:
            return dict(metrics_cache_payload)
    return {
        "service_status": "Off",
        "service_status_class": "stat-red",
        "service_running_status": "inactive",
        "backups_status": "unknown",
        "ram_usage": "unknown",
        "ram_usage_class": "stat-red",
        "cpu_per_core_items": [{"index": 0, "value": "unknown", "class": "stat-red"}],
        "cpu_frequency": "unknown",
        "cpu_frequency_class": "stat-red",
        "storage_usage": "unknown",
        "storage_usage_class": "stat-red",
        "players_online": "unknown",
        "tick_rate": "unknown",
        "session_duration": "--",
        "idle_countdown": "--:--",
        "backup_status": "Idle",
        "backup_status_class": "stat-yellow",
        "last_backup_time": "--",
        "next_backup_time": "--",
        "server_time": get_server_time_text(),
        "rcon_enabled": False,
    }

def format_countdown(seconds):
    # Render remaining seconds as MM:SS.
    if seconds <= 0:
        return "00:00"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"

def get_idle_countdown(service_status=None, players_online=None):
    # Return idle auto-shutdown countdown string for UI.
    if service_status is None:
        service_status = get_status()
    if players_online is None:
        players_online = get_players_online()

    if service_status != "active" or players_online != "0":
        return "--:--"

    with idle_lock:
        if idle_zero_players_since is None:
            return format_countdown(IDLE_ZERO_PLAYERS_SECONDS)
        elapsed = time.time() - idle_zero_players_since

    remaining = IDLE_ZERO_PLAYERS_SECONDS - elapsed
    return format_countdown(remaining)

def idle_player_watcher():
    # Background loop: stop server after sustained zero-player idle time.
    global idle_zero_players_since

    while True:
        try:
            service_status = get_status()
            players_online = get_players_online()
            now = time.time()

            with idle_lock:
                # Count only continuous periods where the server is up and empty.
                if service_status == "active" and players_online == "0":
                    if idle_zero_players_since is None:
                        idle_zero_players_since = now
                    elif now - idle_zero_players_since >= IDLE_ZERO_PLAYERS_SECONDS:
                        stop_server_automatically()
                        idle_zero_players_since = None
                else:
                    idle_zero_players_since = None
        except Exception as exc:
            # Keep watcher alive on transient command failures.
            log_mcweb_exception("idle_player_watcher", exc)

        time.sleep(IDLE_CHECK_INTERVAL_SECONDS)

def start_idle_player_watcher():
    # Start idle watcher in a daemon thread.
    watcher = threading.Thread(target=idle_player_watcher, daemon=True)
    watcher.start()

def backup_session_watcher():
    # Background loop: periodic backups during active sessions.
    #
    #     If a session ends before reaching the backup interval, run one backup at
    #     shutdown so short sessions still produce a backup artifact.
    #     
    global backup_periodic_runs

    while True:
        try:
            now = time.time()
            service_status = get_status()
            is_running = service_status == "active"
            is_off = service_status in ("inactive", "failed")

            should_run_periodic_backup = False
            should_run_shutdown_backup = False
            periodic_due_runs = 0

            session_started_at = read_session_start_time()

            with backup_lock:
                if is_running:
                    # Fixed interval schedule anchored to session start.
                    if session_started_at is not None:
                        due_runs = int(max(0, now - session_started_at) // BACKUP_INTERVAL_SECONDS)
                        if due_runs > backup_periodic_runs:
                            should_run_periodic_backup = True
                            periodic_due_runs = due_runs
                elif is_off and session_started_at is not None:
                    # Session ended (truly off): always run one final backup.
                    # Do not clear during transitional states like activating/deactivating.
                    should_run_shutdown_backup = True

                    clear_session_start_time()
                    backup_periodic_runs = 0

            if should_run_periodic_backup:
                # Mark interval(s) as satisfied only when an actual backup run succeeds.
                if run_backup_script(count_skip_as_success=False):
                    with backup_lock:
                        backup_periodic_runs = max(backup_periodic_runs, periodic_due_runs)

            if should_run_shutdown_backup:
                run_backup_script()
        except Exception as exc:
            # Keep watcher alive on transient command failures.
            log_mcweb_exception("backup_session_watcher", exc)

        time.sleep(15)

def start_backup_session_watcher():
    # Start backup scheduler in a daemon thread.
    watcher = threading.Thread(target=backup_session_watcher, daemon=True)
    watcher.start()

def initialize_session_tracking():
    # Initialize session.txt on process boot with session-preserving rules.
    global backup_periodic_runs
    ensure_session_file()
    service_status = get_status()
    session_start = read_session_start_time()

    # If server is off, clear session start.
    if service_status in OFF_STATES:
        clear_session_start_time()
        return

    # Server is up/transitional: keep existing session anchor if present.
    # Only seed with current time when file is empty/invalid.
    if session_start is None:
        write_session_start_time()
        with backup_lock:
            backup_periodic_runs = 0
        return

    # Startup behavior: when server is already running, skip immediate "catch-up"
    # auto-backup on process restart by aligning counter to current interval index.
    with backup_lock:
        backup_periodic_runs = int(max(0, time.time() - session_start) // BACKUP_INTERVAL_SECONDS)

def _status_debug_note():
    # Return quick status note for troubleshooting session tracking.
    try:
        service_status = get_status()
        session_raw = ""
        if ensure_session_file():
            session_raw = SESSION_FILE.read_text(encoding="utf-8").strip()
        return f"service={service_status}, session_file={'<empty>' if not session_raw else session_raw}"
    except Exception as exc:
        log_mcweb_exception("_status_debug_note", exc)
        return "service=unknown, session_file=unreadable"

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
    global session_tracking_initialized
    if session_tracking_initialized:
        return
    with session_tracking_lock:
        if session_tracking_initialized:
            return
        initialize_session_tracking()
        session_tracking_initialized = True

@app.before_request
def _initialize_session_tracking_before_request():
    # Ensure background state is initialized even under WSGI launch.
    ensure_session_tracking_initialized()
    ensure_metrics_collector_started()
    _ensure_csrf_token()
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _is_csrf_valid():
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
@app.route("/")
def index():
    # Render dashboard page.
    # Legacy query-parameter path (kept for non-AJAX fallback flows).
    message_code = request.args.get("msg", "")
    alert_message = ""
    if message_code == "password_incorrect":
        alert_message = "Password incorrect. Action rejected."
    elif message_code == "csrf_invalid":
        alert_message = "Security check failed. Please refresh and try again."
    elif message_code == "session_write_failed":
        alert_message = "Session file write failed."
    elif message_code == "backup_failed":
        alert_message = "Backup failed."
    elif message_code == "internal_error":
        alert_message = "Internal server error."

    _mark_home_page_client_active()
    data = get_cached_dashboard_metrics()
    return render_template(
        HTML_TEMPLATE_NAME,
        current_page="home",
        service_status=data["service_status"],
        service_status_class=data["service_status_class"],
        service_running_status=data["service_running_status"],
        backups_status=data["backups_status"],
        cpu_per_core_items=data["cpu_per_core_items"],
        cpu_frequency=data["cpu_frequency"],
        cpu_frequency_class=data["cpu_frequency_class"],
        storage_usage=data["storage_usage"],
        storage_usage_class=data["storage_usage_class"],
        players_online=data["players_online"],
        tick_rate=data["tick_rate"],
        session_duration=data["session_duration"],
        idle_countdown=data["idle_countdown"],
        backup_status=data["backup_status"],
        backup_status_class=data["backup_status_class"],
        last_backup_time=data["last_backup_time"],
        next_backup_time=data["next_backup_time"],
        server_time=data["server_time"],
        ram_usage=data["ram_usage"],
        ram_usage_class=data["ram_usage_class"],
        minecraft_logs_raw=get_log_source_text("minecraft"),
        rcon_enabled=data["rcon_enabled"],
        csrf_token=_ensure_csrf_token(),
        alert_message=alert_message,
        alert_message_code=message_code,
        home_page_heartbeat_interval_ms=HOME_PAGE_HEARTBEAT_INTERVAL_MS,
    )


@app.route("/home-heartbeat", methods=["POST"])
def home_heartbeat():
    # Keep metrics collection active while dashboard clients are viewing home.
    _mark_home_page_client_active()
    return ("", 204)

@app.route("/files")
def files_page():
    # Backward-compatible alias for old combined downloads page.
    return redirect("/backups")

@app.route("/favicon.ico")
def favicon():
    # Use a single explicit favicon URL across all pages.
    return redirect(FAVICON_URL)
@app.route("/readme")
def readme_page():
    # Serve local documentation.html page.
    return send_from_directory(str(Path(__file__).resolve().parent), "documentation.html")

@app.route("/backups")
def backups_page():
    # Dedicated backups downloads page.
    ensure_file_page_cache_refresher_started()
    _mark_file_page_client_active()
    return render_template(
        FILES_TEMPLATE_NAME,
        current_page="backups",
        page_title="Backups",
        panel_title="Backups",
        panel_hint="Latest to oldest from /home/marites/backups",
        items=get_cached_file_page_items("backups"),
        download_base="/download/backups",
        empty_text="No backup zip files found.",
        csrf_token=_ensure_csrf_token(),
        file_page_heartbeat_interval_ms=FILE_PAGE_HEARTBEAT_INTERVAL_MS,
    )

@app.route("/crash-logs")
def crash_logs_page():
    # Dedicated crash reports downloads page.
    ensure_file_page_cache_refresher_started()
    _mark_file_page_client_active()
    return render_template(
        FILES_TEMPLATE_NAME,
        current_page="crash_logs",
        page_title="Crash Reports",
        panel_title="Crash Reports",
        panel_hint="Latest to oldest from /opt/Minecraft/crash-reports",
        items=get_cached_file_page_items("crash_logs"),
        download_base="/download/crash-logs",
        empty_text="No crash reports found.",
        csrf_token=_ensure_csrf_token(),
        file_page_heartbeat_interval_ms=FILE_PAGE_HEARTBEAT_INTERVAL_MS,
    )

@app.route("/minecraft-logs")
def minecraft_logs_page():
    # Dedicated Minecraft logs downloads page.
    ensure_file_page_cache_refresher_started()
    _mark_file_page_client_active()
    return render_template(
        FILES_TEMPLATE_NAME,
        current_page="minecraft_logs",
        page_title="Log Files",
        panel_title="Log Files",
        panel_hint="Latest to oldest from /opt/Minecraft/logs",
        items=get_cached_file_page_items("minecraft_logs"),
        download_base="/download/minecraft-logs",
        empty_text="No log files (.log/.gz) found.",
        csrf_token=_ensure_csrf_token(),
        file_page_heartbeat_interval_ms=FILE_PAGE_HEARTBEAT_INTERVAL_MS,
    )

@app.route("/file-page-heartbeat", methods=["POST"])
def file_page_heartbeat():
    # Keep file-list cache refresh active while clients are viewing file pages.
    ensure_file_page_cache_refresher_started()
    _mark_file_page_client_active()
    return ("", 204)

@app.route("/download/backups/<path:filename>", methods=["POST"])
def download_backup(filename):
    sudo_password = request.form.get("sudo_password", "")
    if not validate_sudo_password(sudo_password):
        return _password_rejected_response()
    safe_name = _safe_filename_in_dir(BACKUP_DIR, filename)
    if safe_name is None:
        return abort(404)
    return send_from_directory(str(BACKUP_DIR), safe_name, as_attachment=True)

@app.route("/download/crash-logs/<path:filename>")
def download_crash_log(filename):
    safe_name = _safe_filename_in_dir(CRASH_REPORTS_DIR, filename)
    if safe_name is None:
        return abort(404)
    return send_from_directory(str(CRASH_REPORTS_DIR), safe_name, as_attachment=True)

@app.route("/download/minecraft-logs/<path:filename>")
def download_minecraft_log(filename):
    safe_name = _safe_filename_in_dir(MINECRAFT_LOGS_DIR, filename)
    if safe_name is None:
        return abort(404)
    return send_from_directory(str(MINECRAFT_LOGS_DIR), safe_name, as_attachment=True)

@app.route("/log-stream/<source>")
def log_stream(source):
    settings = _log_source_settings(source)
    if settings is None:
        return Response("invalid log source", status=404)
    ensure_log_stream_fetcher_started(source)
    state = log_stream_states[source]

    # Stream shared source events via SSE (single background fetcher per source).
    def generate():
        last_seq = 0
        while True:
            pending_lines = []
            with state["cond"]:
                state["cond"].wait_for(
                    lambda: state["seq"] > last_seq,
                    timeout=LOG_STREAM_HEARTBEAT_SECONDS,
                )
                current_seq = state["seq"]
                if current_seq > last_seq:
                    if state["events"]:
                        first_available = state["events"][0][0]
                        if last_seq < first_available - 1:
                            last_seq = first_available - 1
                        pending = [(seq, line) for seq, line in state["events"] if seq > last_seq]
                        if pending:
                            pending_lines = [line for _, line in pending]
                            last_seq = pending[-1][0]
                    else:
                        last_seq = current_seq

            if pending_lines:
                for line in pending_lines:
                    yield f"data: {line}\n\n"
            else:
                yield ": keepalive\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

@app.route("/log-text/<source>")
def log_text(source):
    logs = get_log_source_text(source)
    if logs is None:
        return jsonify({"logs": "(no logs)"}), 404
    return jsonify({"logs": logs})

@app.route("/metrics")
def metrics():
    # Return latest shared dashboard metrics snapshot.
    return jsonify(get_cached_dashboard_metrics())

@app.route("/metrics-stream")
def metrics_stream():
    # Stream shared dashboard metric snapshots via SSE.
    def generate():
        global metrics_stream_client_count
        with metrics_cache_cond:
            metrics_stream_client_count += 1
            metrics_cache_cond.notify_all()
        last_seq = -1
        try:
            while True:
                with metrics_cache_cond:
                    metrics_cache_cond.wait_for(
                        lambda: metrics_cache_seq != last_seq,
                        timeout=METRICS_STREAM_HEARTBEAT_SECONDS,
                    )
                    seq = metrics_cache_seq
                    snapshot = dict(metrics_cache_payload) if metrics_cache_payload else None

                if snapshot is None:
                    snapshot = get_cached_dashboard_metrics()
                    with metrics_cache_cond:
                        seq = metrics_cache_seq

                if seq != last_seq and snapshot is not None:
                    payload = json.dumps(snapshot, separators=(",", ":"))
                    yield f"data: {payload}\n\n"
                    last_seq = seq
                else:
                    yield ": keepalive\n\n"
        finally:
            with metrics_cache_cond:
                metrics_stream_client_count = max(0, metrics_stream_client_count - 1)
                metrics_cache_cond.notify_all()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

@app.route("/start", methods=["POST"])
def start():
    # Start Minecraft service and initialize backup session state.
    set_service_status_intent("starting")
    # Start through systemd so status and automation watchers use a single source of truth.
    subprocess.run(["sudo", "systemctl", "start", SERVICE])
    if write_session_start_time() is None:
        log_mcweb_action("start", rejection_message="Session file write failed.")
        return _session_write_failed_response()
    reset_backup_schedule_state()
    log_mcweb_action("start")
    return _ok_response()

@app.route("/stop", methods=["POST"])
def stop():
    # Stop Minecraft service using user-supplied sudo password.
    sudo_password = request.form.get("sudo_password", "")
    if not validate_sudo_password(sudo_password):
        log_mcweb_action("stop", rejection_message="Password incorrect.")
        return _password_rejected_response()

    set_service_status_intent("shutting")
    # Ordered shutdown path: systemd stop first, then final backup.
    graceful_stop_minecraft()
    clear_session_start_time()
    reset_backup_schedule_state()
    log_mcweb_action("stop")
    return _ok_response()

@app.route("/backup", methods=["POST"])
def backup():
    # Run backup script manually from dashboard.
    # Manual backup should not shift the periodic backup schedule anchor.
    if not run_backup_script():
        detail = ""
        with backup_lock:
            detail = backup_last_error
        message = "Backup failed."
        if detail:
            message = f"Backup failed: {detail}"
        log_mcweb_action("backup", rejection_message=message)
        return _backup_failed_response(message)
    log_mcweb_action("backup")
    return _ok_response()

@app.route("/rcon", methods=["POST"])
def rcon():
    # Execute an RCON command after validating sudo password.
    command = request.form.get("rcon_command", "").strip()
    sudo_password = request.form.get("sudo_password", "")
    if not command:
        log_mcweb_action("submit", rejection_message="Command is required.")
        return _rcon_rejected_response("Command is required.", 400)
    if not is_rcon_enabled():
        log_mcweb_action(
            "submit",
            command=command,
            rejection_message="RCON is disabled: rcon.password not found in server.properties.",
        )
        return _rcon_rejected_response(
            "RCON is disabled: rcon.password not found in server.properties.",
            503,
        )
    # Block command execution when the service is not active.
    if get_status() != "active":
        log_mcweb_action("submit", command=command, rejection_message="Server is not running.")
        return _rcon_rejected_response("Server is not running.", 409)
    if not validate_sudo_password(sudo_password):
        log_mcweb_action("submit", command=command, rejection_message="Password incorrect.")
        return _password_rejected_response()

    # Execute through the shared RCON runner using server.properties credentials.
    try:
        result = _run_mcrcon(command, timeout=8)
    except Exception as exc:
        log_mcweb_exception("rcon_execute", exc)
        log_mcweb_action("submit", command=command, rejection_message="RCON command failed to execute.")
        return _rcon_rejected_response("RCON command failed to execute.", 500)

    if result.returncode != 0:
        detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
        message = "RCON command failed."
        if detail:
            message = f"RCON command failed: {detail[:400]}"
        log_mcweb_action("submit", command=command, rejection_message=message)
        return _rcon_rejected_response(message, 500)

    log_mcweb_action("submit", command=command)
    return _ok_response()

if __name__ == "__main__":
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
        app.run(host="0.0.0.0", port=8080)
    except Exception as exc:
        log_mcweb_exception("mcweb_main", exc)
        raise
