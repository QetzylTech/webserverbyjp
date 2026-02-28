"""Service control-plane operations extracted from mcweb."""

from datetime import datetime
from pathlib import Path
import subprocess
import tempfile
import time
import shutil
import zipfile
import threading
import uuid
import re


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


def graceful_stop_minecraft(ctx, trigger="session_end"):
    """Stop service and run a shutdown backup with the provided trigger."""
    systemd_ok = stop_service_systemd(ctx)
    backup_ok = run_backup_script(ctx, trigger=trigger)
    return {"systemd_ok": systemd_ok, "backup_ok": backup_ok}


def stop_server_automatically(ctx, trigger="session_end"):
    """Apply automatic-stop flow (intent, stop, session clear, backup reset)."""
    set_service_status_intent(ctx, "shutting")
    graceful_stop_minecraft(ctx, trigger=trigger)
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
        try:
            direct_result = subprocess.run(
                [ctx.BACKUP_SCRIPT, trigger],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            message = "Backup timed out after 600s."
            with backup_state.lock:
                backup_state.last_error = message
            ctx.log_mcweb_log(
                "backup-timeout",
                command=f"trigger={trigger}",
                rejection_message=message,
            )
            return False
        after_direct_snapshot = get_backup_zip_snapshot(ctx)
        direct_created_zip = backup_snapshot_changed(ctx, before_snapshot, after_direct_snapshot)

        if direct_result.returncode == 0:
            return True
        if direct_created_zip:
            detail = ((direct_result.stderr or "") + "\n" + (direct_result.stdout or "")).strip()
            message = f"Backup completed with warnings (trigger={trigger})."
            if detail:
                message = f"{message} {detail[:400]}"
            ctx.set_backup_warning(message)
            ctx.log_mcweb_action("backup-warning", command=f"trigger={trigger}", rejection_message=message[:700])
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


def _restore_failed(message, error="restore_failed"):
    """Return normalized restore failure payload."""
    return {"ok": False, "error": error, "message": message}


def _detect_server_properties_path(ctx):
    """Return first server.properties path candidate that exists."""
    for path in ctx.SERVER_PROPERTIES_CANDIDATES:
        candidate = Path(path)
        if candidate.exists():
            return candidate
    return None


def _parse_server_properties_kv(text):
    """Parse KEY=VALUE lines from server.properties style content."""
    kv = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        kv[key.strip()] = value.strip()
    return kv


def _update_property_text(original_text, key, value):
    """Replace/add one server.properties key assignment in text."""
    lines = original_text.splitlines()
    target = f"{key}="
    found = False
    out = []
    for line in lines:
        if line.startswith(target):
            out.append(f"{target}{value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{target}{value}")
    return "\n".join(out) + "\n"


def _record_old_world_reference(ctx, old_world_dir, new_world_dir):
    """Append old/new world switch reference to data/old_world.txt."""
    try:
        data_dir = Path(ctx.session_state.session_file).parent
        data_dir.mkdir(parents=True, exist_ok=True)
        log_file = data_dir / "old_world.txt"
        stamp = datetime.now(tz=ctx.DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        line = f"{stamp} | old={old_world_dir} | new={new_world_dir}\n"
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return True
    except OSError:
        return False


def _sanitize_backup_name_component(value):
    """Sanitize filename component for backup/pre-restore artifact names."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_")
    return safe or "world"


def _restore_source_from_extraction(ctx, extract_root):
    """Resolve the extracted world root directory from a backup zip."""
    expected_abs = str(ctx.WORLD_DIR).lstrip("/\\")
    candidates = [
        extract_root / expected_abs,
        extract_root / ctx.WORLD_DIR.name,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    if (extract_root / "level.dat").exists():
        return extract_root

    children = [p for p in extract_root.iterdir() if p.is_dir()]
    if len(children) == 1:
        return children[0]
    return None


def restore_world_backup(ctx, backup_filename, progress_callback=None):
    """Restore backup into a new world dir, switch level-name, and keep old world as reference."""
    def progress(message):
        """Runtime helper progress."""
        if progress_callback:
            try:
                progress_callback(message)
            except Exception:
                pass

    if not ctx.restore_lock.acquire(blocking=False):
        return _restore_failed("A restore operation is already in progress.")

    extract_root = None
    try:
        progress(f"Validating restore source: {backup_filename}")
        safe_name = ctx._safe_filename_in_dir(ctx.BACKUP_DIR, backup_filename)
        if safe_name is None:
            return _restore_failed("Backup file not found.")
        if not safe_name.lower().endswith(".zip"):
            return _restore_failed("Only .zip backups can be restored.")

        backup_zip = ctx.BACKUP_DIR / safe_name
        if is_backup_running(ctx):
            return _restore_failed("Cannot restore while backup is running.")

        world_dir = Path(ctx.WORLD_DIR)
        if not world_dir.exists() or not world_dir.is_dir():
            return _restore_failed(f"WORLD_DIR does not exist: {world_dir}")

        props_path = _detect_server_properties_path(ctx)
        if props_path is None:
            return _restore_failed("server.properties not found; cannot switch level-name.")
        try:
            props_text = props_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return _restore_failed("Failed to read server.properties.")
        props_kv = _parse_server_properties_kv(props_text)
        old_level_name = (props_kv.get("level-name") or "").strip() or world_dir.name

        was_active = ctx.get_status() == "active"
        if was_active and not stop_service_systemd(ctx):
            return _restore_failed("Could not stop service for restore.")
        if was_active:
            progress("Minecraft service stopped for restore.")

        extract_root = Path(tempfile.mkdtemp(prefix="restore_"))
        progress("Extracting backup zip.")
        with zipfile.ZipFile(backup_zip, "r") as zf:
            zf.extractall(extract_root)

        restore_source = _restore_source_from_extraction(ctx, extract_root)
        if restore_source is None:
            return _restore_failed("Could not locate world data inside the selected backup zip.")
        progress(f"Restore source detected: {restore_source}")

        stamp = datetime.now(tz=ctx.DISPLAY_TZ).strftime("%Y-%m-%d_%H-%M-%S")
        world_name = _sanitize_backup_name_component(world_dir.name)
        debug_suffix = "_debug" if bool(getattr(ctx, "DEBUG_ENABLED", False)) else ""
        pre_restore_snapshot = ctx.BACKUP_DIR / f"{world_name}_{stamp}_pre_restore{debug_suffix}.zip"
        progress("Creating pre-restore snapshot.")
        snapshot_result = run_sudo(ctx, ["zip", "-r", str(pre_restore_snapshot), str(world_dir)])
        if snapshot_result.returncode != 0:
            detail = ((snapshot_result.stderr or "") + "\n" + (snapshot_result.stdout or "")).strip()
            message = "Failed to create pre-restore snapshot. Restore cancelled."
            if detail:
                message = f"{message} {detail[:400]}"
            ctx.log_mcweb_action("restore-backup", command=safe_name, rejection_message=message[:700])
            return _restore_failed(message, error="pre_restore_snapshot_failed")
        progress(f"Pre-restore snapshot saved: {pre_restore_snapshot.name}")

        stamp = datetime.now(tz=ctx.DISPLAY_TZ).strftime("%Y-%m-%d_%H-%M-%S")
        new_level_name = f"{old_level_name}_{stamp}"
        new_world_dir = world_dir.parent / new_level_name
        suffix = 1
        while new_world_dir.exists():
            new_world_dir = world_dir.parent / f"{new_level_name}_{suffix}"
            suffix += 1

        progress(f"Applying restore data to new world directory: {new_world_dir.name}.")
        restore_result = run_sudo(
            ctx,
            ["rsync", "-a", "--delete", f"{restore_source}/", f"{new_world_dir}/"],
        )
        if restore_result.returncode != 0:
            return _restore_failed("Restore copy failed while applying backup data.")
        progress("Restore data applied to new world directory.")

        try:
            next_props = _update_property_text(props_text, "level-name", new_world_dir.name)
            props_path.write_text(next_props, encoding="utf-8")
        except OSError:
            return _restore_failed("Restore applied, but failed to update server.properties level-name.")

        if not _record_old_world_reference(ctx, world_dir, new_world_dir):
            ctx.log_mcweb_action(
                "restore-backup",
                command=safe_name,
                rejection_message=f"Restored and switched world, but failed to update old_world.txt for {world_dir}.",
            )
        ctx.WORLD_DIR = new_world_dir
        progress(f"server.properties level-name switched to: {new_world_dir.name}")

        clear_session_start_time(ctx)
        reset_backup_schedule_state(ctx)
        restarted = False
        if was_active:
            progress("Restarting Minecraft service.")
            restart_result = run_sudo(ctx, ["systemctl", "start", ctx.SERVICE])
            ctx.invalidate_status_cache()
            if restart_result.returncode != 0:
                return _restore_failed(
                    "Restore applied, but failed to restart the Minecraft service."
                )
            write_session_start_time(ctx)
            restarted = True
            progress("Minecraft service restarted.")

        progress("Restore completed.")
        return {
            "ok": True,
            "message": "Restore completed successfully.",
            "pre_restore_snapshot": str(pre_restore_snapshot),
            "pre_restore_snapshot_name": pre_restore_snapshot.name,
            "backup_file": safe_name,
            "switched_from_world": str(world_dir),
            "switched_to_world": str(new_world_dir),
            "service_restarted": restarted,
        }
    except zipfile.BadZipFile:
        return _restore_failed("Backup zip is invalid or corrupted.")
    except Exception as exc:
        ctx.log_mcweb_exception("restore_world_backup", exc)
        return _restore_failed("Restore failed due to an internal error.")
    finally:
        if extract_root is not None:
            shutil.rmtree(extract_root, ignore_errors=True)
        ctx.restore_lock.release()


def append_restore_event(ctx, message, *, job_id=None):
    """Append a restore progress event and return its sequence number."""
    entry = {
        "seq": 0,
        "at": datetime.now(tz=ctx.DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "message": str(message or ""),
    }
    with ctx.restore_status_lock:
        if job_id and ctx.restore_status.get("job_id") != job_id:
            return 0
        ctx.restore_status["seq"] = int(ctx.restore_status.get("seq") or 0) + 1
        entry["seq"] = ctx.restore_status["seq"]
        events = list(ctx.restore_status.get("events") or [])
        events.append(entry)
        if len(events) > 500:
            events = events[-500:]
        ctx.restore_status["events"] = events
        return entry["seq"]


def _new_restore_job_id():
    """Return a compact unique identifier for one restore/undo run."""
    return f"restore-{uuid.uuid4().hex[:12]}"


def _start_restore_job_locked(ctx, backup_filename, *, mode):
    """Start a restore worker thread; caller must hold restore_status_lock."""
    job_id = _new_restore_job_id()
    ctx.restore_status["job_id"] = job_id
    ctx.restore_status["running"] = True
    ctx.restore_status["seq"] = 0
    ctx.restore_status["events"] = []
    ctx.restore_status["result"] = None
    if mode != "undo":
        ctx.restore_status["undo_filename"] = ""

    def emit(message):
        """Runtime helper emit."""
        append_restore_event(ctx, message, job_id=job_id)

    def worker():
        """Runtime helper worker."""
        emit(f"{mode.title()} job started for {backup_filename}.")
        result = restore_world_backup(ctx, backup_filename, progress_callback=emit)
        with ctx.restore_status_lock:
            if ctx.restore_status.get("job_id") != job_id:
                return
            ctx.restore_status["running"] = False
            ctx.restore_status["result"] = result
            if result.get("ok") and result.get("pre_restore_snapshot_name"):
                ctx.restore_status["undo_filename"] = result["pre_restore_snapshot_name"]
        if result.get("ok"):
            emit(result.get("message", f"{mode.title()} completed successfully."))
        else:
            emit(result.get("message", f"{mode.title()} failed."))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id}


def start_restore_job(ctx, backup_filename):
    """Start a background restore job for the selected backup zip."""
    safe_name = (backup_filename or "").strip()
    if not safe_name:
        return _restore_failed("Backup filename is required.")
    with ctx.restore_status_lock:
        if ctx.restore_status.get("running"):
            return _restore_failed("A restore operation is already in progress.")
        return _start_restore_job_locked(ctx, safe_name, mode="restore")


def start_undo_restore_job(ctx):
    """Start a background undo job using the latest pre-restore snapshot."""
    with ctx.restore_status_lock:
        if ctx.restore_status.get("running"):
            return _restore_failed("A restore operation is already in progress.")
        snapshot = (ctx.restore_status.get("undo_filename") or "").strip()
        if not snapshot:
            return _restore_failed("Undo is unavailable: no pre-restore snapshot found.")
        return _start_restore_job_locked(ctx, snapshot, mode="undo")


def get_restore_status(ctx, since_seq=0, job_id=None):
    """Return incremental restore status updates for the active/last restore job."""
    try:
        since = int(since_seq or 0)
    except (TypeError, ValueError):
        since = 0
    with ctx.restore_status_lock:
        current_job_id = ctx.restore_status.get("job_id") or ""
        if job_id and job_id != current_job_id:
            return {
                "ok": True,
                "job_id": current_job_id,
                "running": bool(ctx.restore_status.get("running")),
                "events": [],
                "seq": int(ctx.restore_status.get("seq") or 0),
                "result": ctx.restore_status.get("result"),
                "undo_filename": ctx.restore_status.get("undo_filename") or "",
            }
        events = [
            event for event in list(ctx.restore_status.get("events") or [])
            if int(event.get("seq") or 0) > since
        ]
        return {
            "ok": True,
            "job_id": current_job_id,
            "running": bool(ctx.restore_status.get("running")),
            "events": events,
            "seq": int(ctx.restore_status.get("seq") or 0),
            "result": ctx.restore_status.get("result"),
            "undo_filename": ctx.restore_status.get("undo_filename") or "",
        }

