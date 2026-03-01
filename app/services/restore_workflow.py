"""Restore operations and progress orchestration for control plane."""

from datetime import datetime
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import shutil
import zipfile
import threading
import uuid
import re
from app.core import state_store as state_store_service


def run_sudo(ctx, cmd):
    """Run command directly first; fallback to non-interactive sudo when needed."""
    try:
        direct = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
    except Exception:
        direct = None

    if direct is not None and direct.returncode == 0:
        return direct

    return subprocess.run(
        ["sudo", "-n"] + cmd,
        capture_output=True,
        text=True,
    )


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


def ensure_session_file(ctx):
    """Ensure the session tracking file exists and is writable."""
    try:
        session_file = ctx.session_state.session_file
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.touch(exist_ok=True)
        return True
    except OSError:
        return False


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


def reset_backup_schedule_state(ctx):
    """Reset periodic backup run counter for current session."""
    with ctx.backup_state.lock:
        ctx.backup_state.periodic_runs = 0


def is_backup_running(ctx):
    """Return whether backup script reports active run via state file."""
    try:
        ctx.BACKUP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = ctx.BACKUP_STATE_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return False
    return raw == "true"


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


def ensure_startup_rcon_settings(ctx):
    """Ensure startup RCON settings are present and rotate password each start."""
    props_path = _detect_server_properties_path(ctx)
    if props_path is None:
        return {"ok": False, "message": "server.properties not found."}
    try:
        original_text = props_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"ok": False, "message": "Failed to read server.properties."}

    kv = _parse_server_properties_kv(original_text)
    port_value = str(kv.get("rcon.port", "") or "").strip()
    if not port_value.isdigit():
        port_value = "25575"

    password_value = secrets.token_urlsafe(32)

    updated = original_text
    updated = _update_property_text(updated, "enable-rcon", "true")
    updated = _update_property_text(updated, "rcon.port", port_value)
    updated = _update_property_text(updated, "rcon.password", password_value)
    try:
        props_path.write_text(updated, encoding="utf-8")
    except OSError:
        return {"ok": False, "message": "Failed to write server.properties."}

    try:
        ctx._refresh_rcon_config()
    except Exception:
        pass

    return {"ok": True, "path": str(props_path), "rcon_port": port_value}


def _record_restore_history(ctx, backup_name, old_world_dir, archived_old_world_dir, new_world_dir):
    """Append restore world switch reference to data/restore.history."""
    try:
        data_dir = Path(ctx.session_state.session_file).parent
        data_dir.mkdir(parents=True, exist_ok=True)
        log_file = data_dir / "restore.history"
        stamp = datetime.now(tz=ctx.DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        line = (
            f"{stamp} | backup={backup_name} | old={old_world_dir} "
            f"| archived={archived_old_world_dir} | new={new_world_dir}\n"
        )
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return True
    except OSError:
        return False


def _sanitize_backup_name_component(value):
    """Sanitize filename component for backup/pre-restore artifact names."""
    safe = re.sub(r"[^A-Za-z0-9(). _-]+", "_", str(value or "")).strip()
    return safe or "world"


_RESTORE_WORLD_NAME_MAX_LEN = 32
_RESTORE_ID_BODY_LEN = 5
_RESTORE_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _normalize_world_base_name(value):
    """Normalize a world-name base by removing legacy ID suffixes and extra separators."""
    text = str(value or "").strip()
    if not text:
        return "World"
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*\((?:[GR]x)[A-Za-z0-9]{5}\)\s*$", "", text, flags=re.IGNORECASE).strip()
    return text or "World"


def _derive_restore_base_name(backup_filename, restore_source):
    """Derive a readable base name from selected backup filename and extracted source."""
    stem = Path(str(backup_filename or "")).stem.strip()
    stem = re.sub(
        r"(?i)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_debug)?(?:_pre_restore|_prerestore)?$",
        "",
        stem,
    )
    stem = re.sub(r"(?i)(?:_pre_restore|_prerestore)$", "", stem)
    stem = re.sub(r"(?i)[ _]*\((?:[GR]x)[A-Za-z0-9]{5}\)$", "", stem).strip()
    normalized = _normalize_world_base_name(stem)
    if normalized and normalized.lower() != "world":
        return normalized
    if restore_source is not None:
        source_name = _normalize_world_base_name(getattr(restore_source, "name", ""))
        if source_name:
            return source_name
    return normalized


def _compose_restore_world_name(base_name, prefix, code):
    """Build a level-name suffixing with (Gx<id>) or (Rx<id>) and enforce 32-char max."""
    normalized = _normalize_world_base_name(base_name)
    suffix = f" ({prefix}{code})"
    keep = max(1, _RESTORE_WORLD_NAME_MAX_LEN - len(suffix))
    trimmed = normalized[:keep].rstrip(" ._-()")
    if not trimmed:
        trimmed = "World"[:keep]
    return f"{trimmed}{suffix}"


def _new_restore_code(ctx):
    """Generate a unique 5-char alphanumeric restore code tracked in SQLite."""
    db_path = Path(ctx.APP_STATE_DB_PATH)
    for _ in range(128):
        code = "".join(secrets.choice(_RESTORE_ID_ALPHABET) for _ in range(_RESTORE_ID_BODY_LEN))
        if not state_store_service.restore_id_exists(db_path, code):
            return code
    return uuid.uuid4().hex[:_RESTORE_ID_BODY_LEN]


def _archive_old_world_dir(ctx, old_world_dir, archived_world_name):
    """Move previous world directory to data/old_worlds and return destination."""
    data_dir = Path(ctx.session_state.session_file).parent
    old_worlds_dir = data_dir / "old_worlds"
    mkdir_result = run_sudo(ctx, ["mkdir", "-p", str(old_worlds_dir)])
    if mkdir_result.returncode != 0:
        return None, "Failed to create old_worlds archive directory."

    base_name = str(archived_world_name or old_world_dir.name).strip() or old_world_dir.name
    archived_old_world_dir = old_worlds_dir / base_name
    suffix = 1
    while archived_old_world_dir.exists():
        archived_old_world_dir = old_worlds_dir / f"{base_name}_{suffix}"
        suffix += 1

    move_result = run_sudo(ctx, ["mv", str(old_world_dir), str(archived_old_world_dir)])
    if move_result.returncode != 0:
        return None, "Failed to archive previous world directory."
    return archived_old_world_dir, ""


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
    """Restore backup into a new world dir, switch level-name, and archive old world."""
    def progress(message):
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

        stored_id = _new_restore_code(ctx)
        active_id = _new_restore_code(ctx)
        while active_id == stored_id:
            active_id = _new_restore_code(ctx)
        stored_world_name = _compose_restore_world_name(old_level_name, "Gx", stored_id)
        restore_base_name = _derive_restore_base_name(safe_name, restore_source)
        active_world_name = _compose_restore_world_name(restore_base_name, "Rx", active_id)

        stamp = datetime.now(tz=ctx.DISPLAY_TZ).strftime("%Y-%m-%d_%H-%M-%S")
        snapshot_base = _sanitize_backup_name_component(stored_world_name)
        debug_suffix = "_debug" if bool(getattr(ctx, "DEBUG_ENABLED", False)) else ""
        pre_restore_snapshot = ctx.BACKUP_DIR / f"{snapshot_base}_{stamp}_prerestore{debug_suffix}.zip"
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

        new_level_name = active_world_name
        new_world_dir = world_dir.parent / new_level_name
        while new_world_dir.exists():
            active_id = _new_restore_code(ctx)
            if active_id == stored_id:
                continue
            new_level_name = _compose_restore_world_name(restore_base_name, "Rx", active_id)
            new_world_dir = world_dir.parent / new_level_name

        progress(f"Applying restore data to new world directory: {new_world_dir.name}.")
        restore_result = run_sudo(
            ctx,
            ["rsync", "-a", "--delete", f"{restore_source}/", f"{new_world_dir}/"],
        )
        if restore_result.returncode != 0:
            return _restore_failed("Restore copy failed while applying backup data.")
        progress("Restore data applied to new world directory.")

        progress("Archiving previous world directory.")
        archived_old_world_dir, archive_err = _archive_old_world_dir(ctx, world_dir, stored_world_name)
        if archived_old_world_dir is None:
            return _restore_failed(archive_err or "Failed to archive previous world directory.")

        try:
            next_props = _update_property_text(props_text, "level-name", new_world_dir.name)
            props_path.write_text(next_props, encoding="utf-8")
        except OSError:
            rollback_result = run_sudo(ctx, ["mv", str(archived_old_world_dir), str(world_dir)])
            if rollback_result.returncode != 0:
                return _restore_failed(
                    "Restore applied, but failed to update server.properties level-name and failed to rollback archived world."
                )
            return _restore_failed("Restore applied, but failed to update server.properties level-name.")

        if not _record_restore_history(ctx, safe_name, world_dir, archived_old_world_dir, new_world_dir):
            ctx.log_mcweb_action(
                "restore-backup",
                command=safe_name,
                rejection_message=(
                    f"Restored and switched world, but failed to update restore.history for {world_dir} "
                    f"-> {archived_old_world_dir}."
                ),
            )
        try:
            state_store_service.append_restore_name_run(
                Path(ctx.APP_STATE_DB_PATH),
                {
                    "backup_filename": safe_name,
                    "restore_source_name": getattr(restore_source, "name", ""),
                    "previous_world_name": old_level_name,
                    "stored_world_name": stored_world_name,
                    "stored_id": stored_id,
                    "active_world_name": new_world_dir.name,
                    "active_id": active_id,
                    "pre_restore_snapshot_name": pre_restore_snapshot.name,
                    "archived_old_world_name": archived_old_world_dir.name,
                },
            )
        except Exception as exc:
            ctx.log_mcweb_exception("append_restore_name_run", exc)
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
            "archived_old_world": str(archived_old_world_dir),
            "switched_to_world": str(new_world_dir),
            "stored_world_name": stored_world_name,
            "stored_restore_id": f"Gx{stored_id}",
            "active_restore_id": f"Rx{active_id}",
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
    """Return a compact unique identifier for one restore run."""
    return f"restore-{uuid.uuid4().hex[:12]}"


def _start_restore_job_locked(ctx, backup_filename):
    """Start a restore worker thread; caller must hold restore_status_lock."""
    job_id = _new_restore_job_id()
    ctx.restore_status["job_id"] = job_id
    ctx.restore_status["running"] = True
    ctx.restore_status["seq"] = 0
    ctx.restore_status["events"] = []
    ctx.restore_status["result"] = None

    def emit(message):
        append_restore_event(ctx, message, job_id=job_id)

    def worker():
        emit(f"Restore job started for {backup_filename}.")
        result = restore_world_backup(ctx, backup_filename, progress_callback=emit)
        try:
            state_store_service.append_restore_run(
                Path(ctx.APP_STATE_DB_PATH),
                {
                    "job_id": job_id,
                    "mode": "restore",
                    "backup_filename": backup_filename,
                    "ok": bool(result.get("ok")),
                    "error_code": str(result.get("error", "") or ""),
                    "message": str(result.get("message", "") or ""),
                    "pre_restore_snapshot_name": str(result.get("pre_restore_snapshot_name", "") or ""),
                    "switched_from_world": str(result.get("switched_from_world", "") or ""),
                    "archived_old_world": str(result.get("archived_old_world", "") or ""),
                    "switched_to_world": str(result.get("switched_to_world", "") or ""),
                    "stored_restore_id": str(result.get("stored_restore_id", "") or ""),
                    "active_restore_id": str(result.get("active_restore_id", "") or ""),
                },
            )
        except Exception as exc:
            ctx.log_mcweb_exception("append_restore_run", exc)
        if bool(result.get("ok")):
            try:
                db_match = state_store_service.restore_backup_records_match(
                    Path(ctx.APP_STATE_DB_PATH),
                    backup_filename=result.get("backup_file", backup_filename),
                    pre_restore_snapshot_name=result.get("pre_restore_snapshot_name", ""),
                    stored_restore_id=result.get("stored_restore_id", ""),
                    active_restore_id=result.get("active_restore_id", ""),
                )
                result["db_record_match"] = bool(db_match)
                if not db_match:
                    emit("Warning: restore record does not fully match backup records in sqlite.")
            except Exception as exc:
                ctx.log_mcweb_exception("restore_backup_records_match", exc)
                result["db_record_match"] = False
        with ctx.restore_status_lock:
            if ctx.restore_status.get("job_id") != job_id:
                return
            ctx.restore_status["running"] = False
            ctx.restore_status["result"] = result
        if result.get("ok"):
            emit(result.get("message", "Restore completed successfully."))
        else:
            emit(result.get("message", "Restore failed."))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id}


def start_restore_job(ctx, backup_filename):
    """Start a background restore job for the selected backup zip."""
    safe_name = (backup_filename or "").strip()
    if not safe_name:
        result = _restore_failed("Backup filename is required.")
        try:
            state_store_service.append_restore_run(
                Path(ctx.APP_STATE_DB_PATH),
                {
                    "job_id": "",
                    "mode": "restore",
                    "backup_filename": safe_name,
                    "ok": False,
                    "error_code": str(result.get("error", "") or ""),
                    "message": str(result.get("message", "") or ""),
                },
            )
        except Exception as exc:
            ctx.log_mcweb_exception("append_restore_run", exc)
        return result
    with ctx.restore_status_lock:
        if ctx.restore_status.get("running"):
            result = _restore_failed("A restore operation is already in progress.")
            try:
                state_store_service.append_restore_run(
                    Path(ctx.APP_STATE_DB_PATH),
                    {
                        "job_id": "",
                        "mode": "restore",
                        "backup_filename": safe_name,
                        "ok": False,
                        "error_code": str(result.get("error", "") or ""),
                        "message": str(result.get("message", "") or ""),
                    },
                )
            except Exception as exc:
                ctx.log_mcweb_exception("append_restore_run", exc)
            return result
        return _start_restore_job_locked(ctx, safe_name)


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
        }
