"""Service control-plane operations extracted from mcweb."""

from datetime import datetime
import subprocess
import time


def set_service_status_intent(ctx, intent):
    """Set the current lifecycle intent (starting/shutting/crashed/etc)."""
    with ctx.service_status_intent_lock:
        ctx.service_status_intent = intent


def get_service_status_intent(ctx):
    """Read the lifecycle intent with lock protection."""
    with ctx.service_status_intent_lock:
        return ctx.service_status_intent


def stop_service_systemd(ctx):
    """Stop the systemd service and wait briefly for an off-state."""
    try:
        run_sudo(ctx, ["systemctl", "stop", ctx.SERVICE])
        ctx.invalidate_status_cache()
    except Exception as exc:
        ctx.log_mcweb_exception("stop_service_systemd", exc)

    deadline = time.time() + 10
    while time.time() < deadline:
        if ctx.get_status() in ctx.OFF_STATES:
            return True
        time.sleep(0.5)
    return False


def get_sudo_password(ctx):
    """Resolve the sudo password from active RCON/server properties config."""
    password, _, enabled = ctx._refresh_rcon_config()
    if not enabled or not password:
        return None
    return password


def run_sudo(ctx, cmd):
    """Run a privileged command by piping the resolved sudo password."""
    sudo_password = get_sudo_password(ctx)
    if not sudo_password:
        raise RuntimeError("sudo password unavailable: rcon.password not found in server.properties")
    return subprocess.run(
        ["sudo", "-S"] + cmd,
        input=f"{sudo_password}\n",
        capture_output=True,
        text=True,
    )


def validate_sudo_password(ctx, sudo_password):
    """Validate user-supplied password against the resolved sudo password."""
    expected_password = get_sudo_password(ctx)
    if not expected_password:
        return False
    return (sudo_password or "").strip() == expected_password


def ensure_session_file(ctx):
    """Ensure the session tracking file exists and is writable."""
    try:
        session_file = ctx.session_state.session_file
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.touch(exist_ok=True)
        return True
    except OSError:
        return False


def read_session_start_time(ctx):
    """Read session start epoch seconds from the session tracking file."""
    if not ensure_session_file(ctx):
        return None
    try:
        raw = ctx.session_state.session_file.read_text(encoding="utf-8").strip()
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
    if ts > 1_000_000_000_000:
        ts = ts / 1000.0
    return ts


def write_session_start_time(ctx, timestamp=None):
    """Write session start epoch seconds and return the stored value."""
    if not ensure_session_file(ctx):
        return None
    ts = time.time() if timestamp is None else float(timestamp)
    try:
        ctx.session_state.session_file.write_text(f"{ts:.6f}\n", encoding="utf-8")
    except OSError:
        return None
    return ts


def clear_session_start_time(ctx):
    """Clear the session tracking file."""
    if not ensure_session_file(ctx):
        return False
    try:
        ctx.session_state.session_file.write_text("", encoding="utf-8")
    except OSError:
        return False
    return True


def get_session_start_time(ctx, service_status=None):
    """Return session start time only when the service is logically on."""
    if service_status is None:
        service_status = ctx.get_status()
    if service_status in ctx.OFF_STATES:
        return None
    return read_session_start_time(ctx)


def get_session_duration_text(ctx):
    """Return ``HH:MM:SS`` elapsed session duration or ``--`` when unset."""
    start_time = read_session_start_time(ctx)
    if start_time is None:
        return "--"
    elapsed = max(0, int(time.time() - start_time))
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def graceful_stop_minecraft(ctx):
    """Stop service and run a session-end backup."""
    systemd_ok = stop_service_systemd(ctx)
    backup_ok = run_backup_script(ctx, trigger="session_end")
    return {"systemd_ok": systemd_ok, "backup_ok": backup_ok}


def stop_server_automatically(ctx):
    """Apply automatic-stop flow (intent, stop, session clear, backup reset)."""
    set_service_status_intent(ctx, "shutting")
    graceful_stop_minecraft(ctx)
    clear_session_start_time(ctx)
    reset_backup_schedule_state(ctx)


def run_backup_script(ctx, count_skip_as_success=True, trigger="manual"):
    """Run backup script with single-flight locking and snapshot verification."""
    backup_state = ctx.backup_state
    # Non-blocking lock avoids backup piling when watchers and manual actions race.
    if not backup_state.run_lock.acquire(blocking=False):
        return bool(count_skip_as_success)
    try:
        if is_backup_running(ctx):
            with backup_state.lock:
                backup_state.last_error = ""
            return bool(count_skip_as_success)

        with backup_state.lock:
            backup_state.last_error = ""

        before_snapshot = get_backup_zip_snapshot(ctx)
        direct_result = subprocess.run(
            [ctx.BACKUP_SCRIPT, trigger],
            capture_output=True,
            text=True,
            timeout=600,
        )
        after_direct_snapshot = get_backup_zip_snapshot(ctx)
        direct_created_zip = backup_snapshot_changed(ctx, before_snapshot, after_direct_snapshot)

        if direct_result.returncode == 0 or direct_created_zip:
            return True
        err = ((direct_result.stderr or "") + "\n" + (direct_result.stdout or "")).strip()
        with backup_state.lock:
            backup_state.last_error = err[:700] if err else "Backup command returned non-zero exit status."
        return False
    finally:
        backup_state.run_lock.release()


def format_backup_time(ctx, timestamp):
    """Format epoch seconds in configured display timezone."""
    if timestamp is None:
        return "--"
    return datetime.fromtimestamp(timestamp, tz=ctx.DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z")


def get_server_time_text(ctx):
    """Return current server time in configured display timezone."""
    return datetime.now(tz=ctx.DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z")


def get_latest_backup_zip_timestamp(ctx):
    """Return latest backup zip modification timestamp."""
    backup_dir = ctx.BACKUP_DIR
    if not backup_dir.exists() or not backup_dir.is_dir():
        return None
    latest = None
    for path in backup_dir.glob("*.zip"):
        try:
            ts = path.stat().st_mtime
        except OSError:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def get_backup_zip_snapshot(ctx):
    """Capture ``path -> mtime_ns`` snapshot for backup output verification."""
    snapshot = {}
    backup_dir = ctx.BACKUP_DIR
    if not backup_dir.exists() or not backup_dir.is_dir():
        return snapshot
    for path in backup_dir.glob("*.zip"):
        try:
            snapshot[str(path)] = path.stat().st_mtime_ns
        except OSError:
            continue
    return snapshot


def backup_snapshot_changed(ctx, before_snapshot, after_snapshot):
    """Return True when any backup zip was created or modified."""
    if not before_snapshot and after_snapshot:
        return True
    for file_path, after_mtime in after_snapshot.items():
        before_mtime = before_snapshot.get(file_path)
        if before_mtime is None:
            return True
        if after_mtime != before_mtime:
            return True
    return False


def get_backup_schedule_times(ctx, service_status=None):
    """Return formatted last/next backup schedule timestamps."""
    if service_status is None:
        service_status = ctx.get_status()

    latest_zip_ts = get_latest_backup_zip_timestamp(ctx)
    last_backup_ts = latest_zip_ts
    next_backup_at = None
    if service_status not in ctx.OFF_STATES:
        session_start = get_session_start_time(ctx, service_status)
        if session_start is not None:
            elapsed_intervals = int(max(0, time.time() - session_start) // ctx.BACKUP_INTERVAL_SECONDS)
            next_backup_at = session_start + ((elapsed_intervals + 1) * ctx.BACKUP_INTERVAL_SECONDS)

    return {
        "last_backup_time": format_backup_time(ctx, last_backup_ts),
        "next_backup_time": format_backup_time(ctx, next_backup_at),
    }


def get_backup_status(ctx):
    """Return backup runtime status text and CSS class."""
    if is_backup_running(ctx):
        return "Running", "stat-green"
    return "Idle", "stat-yellow"


def is_backup_running(ctx):
    """Return whether backup script reports active run via state file."""
    try:
        ctx.BACKUP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = ctx.BACKUP_STATE_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return False
    return raw == "true"


def reset_backup_schedule_state(ctx):
    """Reset periodic backup run counter for current session."""
    with ctx.backup_state.lock:
        ctx.backup_state.periodic_runs = 0

