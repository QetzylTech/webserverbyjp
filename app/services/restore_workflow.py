"""Restore operations and progress orchestration for control plane."""

from datetime import datetime
from pathlib import Path
import shutil
import tempfile
import threading
import uuid
import zipfile

from app.core import state_store as state_store_service
from app.services.restore_workflow_helpers import (
    _archive_old_world_dir,
    _compose_restore_world_name,
    _derive_restore_base_name,
    _detect_server_properties_path,
    _new_restore_code,
    _parse_server_properties_kv,
    _record_restore_history,
    _restore_failed,
    _restore_source_from_extraction,
    _sanitize_backup_name_component,
    _update_property_text,
    clear_session_start_time,
    is_backup_running,
    reset_backup_schedule_state,
    run_sudo,
    stop_service_systemd,
    write_session_start_time,
)

SNAPSHOT_TOKEN_PREFIX = "snapshot::"


def _safe_extract_zip(zip_file, destination):
    """Extract zip members under destination only (blocks path traversal)."""
    dest_resolved = Path(destination).resolve()
    for member in zip_file.infolist():
        name = str(member.filename or "")
        if not name or "\x00" in name:
            raise ValueError("Invalid zip entry.")
        target = (dest_resolved / name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError as exc:
            raise ValueError("Unsafe path in zip archive.") from exc
    zip_file.extractall(dest_resolved)


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
    was_active = False
    restore_succeeded = False
    service_stopped_for_restore = False

    try:
        def fail(message, error="restore_failed"):
            nonlocal service_stopped_for_restore
            if was_active and service_stopped_for_restore:
                restart_result = run_sudo(ctx, ["systemctl", "start", ctx.SERVICE])
                ctx.invalidate_status_cache()
                if restart_result.returncode == 0:
                    write_session_start_time(ctx)
                    service_stopped_for_restore = False
                else:
                    message = f"{message} Service restart after failed restore also failed."
            return _restore_failed(message, error=error)

        selected_name = str(backup_filename or "").strip()
        progress(f"Validating restore source: {selected_name}")
        is_snapshot = selected_name.startswith(SNAPSHOT_TOKEN_PREFIX)
        safe_name = ""
        snapshot_dir = None
        backup_zip = None
        restore_source_name = ""

        if is_snapshot:
            raw_snapshot_name = selected_name[len(SNAPSHOT_TOKEN_PREFIX):].strip()
            snapshot_name = Path(raw_snapshot_name).name
            if not snapshot_name or snapshot_name != raw_snapshot_name:
                return fail("Snapshot not found.")
            snapshot_root = Path(getattr(ctx, "AUTO_SNAPSHOT_DIR", "") or (ctx.BACKUP_DIR / "snapshots"))
            snapshot_dir = snapshot_root / snapshot_name
            try:
                snapshot_root_resolved = snapshot_root.resolve()
                snapshot_dir_resolved = snapshot_dir.resolve()
                snapshot_dir_resolved.relative_to(snapshot_root_resolved)
            except (OSError, ValueError):
                return fail("Snapshot not found.")
            if not snapshot_dir_resolved.exists() or not snapshot_dir_resolved.is_dir():
                return fail("Snapshot not found.")
            safe_name = snapshot_name
            snapshot_dir = snapshot_dir_resolved
            restore_source_name = snapshot_name
        else:
            safe_name = ctx._safe_filename_in_dir(ctx.BACKUP_DIR, selected_name)
            if safe_name is None:
                return fail("Backup file not found.")
            if not safe_name.lower().endswith(".zip"):
                return fail("Only .zip backups can be restored.")
            backup_zip = ctx.BACKUP_DIR / safe_name
            restore_source_name = safe_name

        if is_backup_running(ctx):
            return fail("Cannot restore while backup is running.")

        world_dir = Path(ctx.WORLD_DIR)
        if not world_dir.exists() or not world_dir.is_dir():
            return fail(f"WORLD_DIR does not exist: {world_dir}")

        props_path = _detect_server_properties_path(ctx)
        if props_path is None:
            return fail("server.properties not found; cannot switch level-name.")
        try:
            props_text = props_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return fail("Failed to read server.properties.")
        props_kv = _parse_server_properties_kv(props_text)
        old_level_name = (props_kv.get("level-name") or "").strip() or world_dir.name

        was_active = ctx.get_status() == "active"
        if was_active and not stop_service_systemd(ctx):
            return fail("Could not stop service for restore.")
        if was_active:
            service_stopped_for_restore = True
            progress("Minecraft service stopped for restore.")

        if snapshot_dir is not None:
            restore_source = snapshot_dir
            progress(f"Restore source detected: {restore_source}")
        else:
            extract_root = Path(tempfile.mkdtemp(prefix="restore_"))
            progress("Extracting backup zip.")
            with zipfile.ZipFile(backup_zip, "r") as zf:
                _safe_extract_zip(zf, extract_root)

            restore_source = _restore_source_from_extraction(ctx, extract_root)
            if restore_source is None:
                return fail("Could not locate world data inside the selected backup zip.")
            progress(f"Restore source detected: {restore_source}")

        stored_id = _new_restore_code(ctx)
        active_id = _new_restore_code(ctx)
        while active_id == stored_id:
            active_id = _new_restore_code(ctx)
        stored_world_name = _compose_restore_world_name(old_level_name, "Gx", stored_id)
        restore_base_name = _derive_restore_base_name(restore_source_name, restore_source)
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
            return fail(message, error="pre_restore_snapshot_failed")
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
            return fail("Restore copy failed while applying backup data.")
        progress("Restore data applied to new world directory.")

        progress("Archiving previous world directory.")
        archived_old_world_dir, archive_err = _archive_old_world_dir(ctx, world_dir, stored_world_name)
        if archived_old_world_dir is None:
            return fail(archive_err or "Failed to archive previous world directory.")

        try:
            next_props = _update_property_text(props_text, "level-name", new_world_dir.name)
            props_path.write_text(next_props, encoding="utf-8")
        except OSError:
            rollback_result = run_sudo(ctx, ["mv", str(archived_old_world_dir), str(world_dir)])
            if rollback_result.returncode != 0:
                return fail(
                    "Restore applied, but failed to update server.properties level-name and failed to rollback archived world."
                )
            return fail("Restore applied, but failed to update server.properties level-name.")

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
                    "backup_filename": selected_name,
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
                return fail(
                    "Restore applied, but failed to restart the Minecraft service."
                )
            write_session_start_time(ctx)
            restarted = True
            service_stopped_for_restore = False
            progress("Minecraft service restarted.")

        restore_succeeded = True
        progress("Restore completed.")
        return {
            "ok": True,
            "message": "Restore completed successfully.",
            "pre_restore_snapshot": str(pre_restore_snapshot),
            "pre_restore_snapshot_name": pre_restore_snapshot.name,
            "backup_file": selected_name,
            "switched_from_world": str(world_dir),
            "archived_old_world": str(archived_old_world_dir),
            "switched_to_world": str(new_world_dir),
            "stored_world_name": stored_world_name,
            "stored_restore_id": f"Gx{stored_id}",
            "active_restore_id": f"Rx{active_id}",
            "service_restarted": restarted,
        }
    except zipfile.BadZipFile:
        if was_active and service_stopped_for_restore and not restore_succeeded:
            restart_result = run_sudo(ctx, ["systemctl", "start", ctx.SERVICE])
            ctx.invalidate_status_cache()
            if restart_result.returncode == 0:
                write_session_start_time(ctx)
                service_stopped_for_restore = False
        return _restore_failed("Backup zip is invalid or corrupted.")
    except ValueError:
        if was_active and service_stopped_for_restore and not restore_succeeded:
            restart_result = run_sudo(ctx, ["systemctl", "start", ctx.SERVICE])
            ctx.invalidate_status_cache()
            if restart_result.returncode == 0:
                write_session_start_time(ctx)
                service_stopped_for_restore = False
        return _restore_failed("Backup zip contains unsafe paths.")
    except Exception as exc:
        ctx.log_mcweb_exception("restore_world_backup", exc)
        if was_active and service_stopped_for_restore and not restore_succeeded:
            restart_result = run_sudo(ctx, ["systemctl", "start", ctx.SERVICE])
            ctx.invalidate_status_cache()
            if restart_result.returncode == 0:
                write_session_start_time(ctx)
                service_stopped_for_restore = False
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
        if bool(result.get("ok")) and not str(result.get("backup_file", "")).startswith(SNAPSHOT_TOKEN_PREFIX):
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
