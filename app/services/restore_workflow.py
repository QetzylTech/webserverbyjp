"""Restore operations and progress orchestration for control plane."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import uuid
import zipfile

from app.core import state_store as state_store_service
from app.ports import ports
from app.services.worker_scheduler import start_detached
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
    start_service,
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


def _create_pre_restore_snapshot(world_path, snapshot_zip_path):
    source = Path(world_path)
    target = Path(snapshot_zip_path)
    if not source.exists() or not source.is_dir():
        return False, "World directory not found for snapshot."
    target.parent.mkdir(parents=True, exist_ok=True)
    archive_path = ports.filesystem.make_zip_archive(
        target.with_suffix(""),
        root_dir=source.parent,
        base_dir=source.name,
    )
    if Path(archive_path).resolve() != target.resolve():
        Path(archive_path).replace(target)
    return True, ""


def _copy_world_tree(source_dir, target_dir):
    src = Path(source_dir)
    dst = Path(target_dir)
    if dst.exists():
        ports.filesystem.rmtree(dst, ignore_errors=True)
    ports.filesystem.copytree(src, dst)


def _emit_progress(progress_callback, message):
    if not progress_callback:
        return
    try:
        progress_callback(message)
    except Exception:
        pass


def _restart_service_after_failure(ctx, restore_state):
    if not restore_state.was_active or not restore_state.service_stopped_for_restore:
        return True
    restart_result = start_service(ctx)
    ctx.invalidate_status_cache()
    if restart_result.returncode != 0:
        return False
    write_session_start_time(ctx)
    restore_state.service_stopped_for_restore = False
    return True


def _restore_failure(ctx, restore_state, message, *, error="restore_failed"):
    if not _restart_service_after_failure(ctx, restore_state):
        message = f"{message} Service restart after failed restore also failed."
    return _restore_failed(message, error=error)


def _resolve_selected_source(ctx, selected_name):
    if selected_name.startswith(SNAPSHOT_TOKEN_PREFIX):
        raw_snapshot_name = selected_name[len(SNAPSHOT_TOKEN_PREFIX):].strip()
        snapshot_name = Path(raw_snapshot_name).name
        if not snapshot_name or snapshot_name != raw_snapshot_name:
            return None, None, "", "Snapshot not found."
        snapshot_root = Path(getattr(ctx, "AUTO_SNAPSHOT_DIR", "") or (ctx.BACKUP_DIR / "snapshots"))
        snapshot_dir = snapshot_root / snapshot_name
        try:
            snapshot_root_resolved = snapshot_root.resolve()
            snapshot_dir_resolved = snapshot_dir.resolve()
            snapshot_dir_resolved.relative_to(snapshot_root_resolved)
        except (OSError, ValueError):
            return None, None, "", "Snapshot not found."
        if not snapshot_dir_resolved.exists() or not snapshot_dir_resolved.is_dir():
            return None, None, "", "Snapshot not found."
        return snapshot_name, snapshot_dir_resolved, snapshot_name, ""

    safe_name = ctx._safe_filename_in_dir(ctx.BACKUP_DIR, selected_name)
    if safe_name is None:
        return None, None, "", "Backup file not found."
    if not safe_name.lower().endswith(".zip"):
        return None, None, "", "Only .zip backups can be restored."
    return safe_name, ctx.BACKUP_DIR / safe_name, safe_name, ""


def _load_restore_context(ctx):
    world_dir = Path(ctx.WORLD_DIR)
    if not world_dir.exists() or not world_dir.is_dir():
        return None, None, None, None, f"WORLD_DIR does not exist: {world_dir}"

    props_path = _detect_server_properties_path(ctx)
    if props_path is None:
        return None, None, None, None, "server.properties not found; cannot switch level-name."
    try:
        props_text = props_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None, None, None, None, "Failed to read server.properties."

    props_kv = _parse_server_properties_kv(props_text)
    old_level_name = (props_kv.get("level-name") or "").strip() or world_dir.name
    return world_dir, props_path, props_text, old_level_name, ""


def _prepare_restore_source(ctx, selected_name, source_entry, restore_state, progress):
    if selected_name.startswith(SNAPSHOT_TOKEN_PREFIX):
        progress(f"Restore source detected: {source_entry}")
        return None, source_entry, None

    extract_root = ports.filesystem.mkdtemp(prefix="restore_")
    progress("Extracting backup zip.")
    with zipfile.ZipFile(source_entry, "r") as zf:
        _safe_extract_zip(zf, extract_root)

    restore_source = _restore_source_from_extraction(ctx, extract_root)
    if restore_source is None:
        return extract_root, None, _restore_failure(
            ctx,
            restore_state,
            "Could not locate world data inside the selected backup zip.",
        )
    progress(f"Restore source detected: {restore_source}")
    return extract_root, restore_source, None


def _reserve_restore_names(ctx, old_level_name, restore_source_name, restore_source):
    stored_id = _new_restore_code(ctx)
    active_id = _new_restore_code(ctx)
    while active_id == stored_id:
        active_id = _new_restore_code(ctx)
    stored_world_name = _compose_restore_world_name(old_level_name, "Gx", stored_id)
    restore_base_name = _derive_restore_base_name(restore_source_name, restore_source)
    return stored_id, active_id, stored_world_name, restore_base_name


def _create_snapshot_for_restore(ctx, world_dir, stored_world_name, safe_name, progress, fail):
    stamp = datetime.now(tz=ctx.DISPLAY_TZ).strftime("%Y-%m-%d_%H-%M-%S")
    snapshot_base = _sanitize_backup_name_component(stored_world_name)
    pre_restore_snapshot = ctx.BACKUP_DIR / f"{snapshot_base}_{stamp}_prerestore.zip"
    progress("Creating pre-restore snapshot.")
    snapshot_ok, snapshot_err = _create_pre_restore_snapshot(world_dir, pre_restore_snapshot)
    if snapshot_ok:
        progress(f"Pre-restore snapshot saved: {pre_restore_snapshot.name}")
        return pre_restore_snapshot, None

    message = "Failed to create pre-restore snapshot. Restore cancelled."
    if snapshot_err:
        message = f"{message} {snapshot_err[:400]}"
    ctx.log_mcweb_action("restore-backup", command=safe_name, rejection_message=message[:700])
    return None, fail(message, error="pre_restore_snapshot_failed")


def _next_restore_world_dir(ctx, restore_base_name, stored_id, active_id, world_parent):
    while True:
        new_world_name = _compose_restore_world_name(restore_base_name, "Rx", active_id)
        new_world_dir = world_parent / new_world_name
        if not new_world_dir.exists():
            return active_id, new_world_dir
        active_id = _new_restore_code(ctx)
        if active_id == stored_id:
            continue


def _apply_restore_data(restore_source, new_world_dir, progress, fail):
    progress(f"Applying restore data to new world directory: {new_world_dir.name}.")
    try:
        _copy_world_tree(restore_source, new_world_dir)
    except Exception:
        return fail("Restore copy failed while applying backup data.")
    progress("Restore data applied to new world directory.")
    return None


def _switch_server_properties(props_path, props_text, world_dir, archived_old_world_dir, new_world_dir, fail):
    try:
        next_props = _update_property_text(props_text, "level-name", new_world_dir.name)
        props_path.write_text(next_props, encoding="utf-8")
    except OSError:
        try:
            ports.filesystem.move(archived_old_world_dir, world_dir)
        except Exception:
            return fail(
                "Restore applied, but failed to update server.properties level-name and failed to rollback archived world."
            )
        return fail("Restore applied, but failed to update server.properties level-name.")
    return None


def _append_restore_name_run(
    ctx,
    *,
    selected_name,
    restore_source,
    old_level_name,
    stored_world_name,
    stored_id,
    new_world_dir,
    active_id,
    pre_restore_snapshot,
    archived_old_world_dir,
):
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


def _restart_after_success(ctx, restore_state, progress, fail):
    clear_session_start_time(ctx)
    reset_backup_schedule_state(ctx)
    if not restore_state.was_active:
        return False, None

    progress("Restarting Minecraft service.")
    restart_result = start_service(ctx)
    ctx.invalidate_status_cache()
    if restart_result.returncode != 0:
        return False, fail("Restore applied, but failed to restart the Minecraft service.")
    write_session_start_time(ctx)
    restore_state.service_stopped_for_restore = False
    progress("Minecraft service restarted.")
    return True, None


def restore_world_backup(ctx, backup_filename, progress_callback=None):
    """Restore backup into a new world dir, switch level-name, and archive old world."""
    progress = lambda message: _emit_progress(progress_callback, message)

    if not ctx.restore_lock.acquire(blocking=False):
        return _restore_failed("A restore operation is already in progress.")

    extract_root = None
    restore_state = SimpleNamespace(
        was_active=False,
        restore_succeeded=False,
        service_stopped_for_restore=False,
    )

    try:
        def fail(message, error="restore_failed"):
            return _restore_failure(ctx, restore_state, message, error=error)

        selected_name = str(backup_filename or "").strip()
        progress(f"Validating restore source: {selected_name}")

        safe_name, source_entry, restore_source_name, error_message = _resolve_selected_source(ctx, selected_name)
        if error_message:
            return fail(error_message)
        if is_backup_running(ctx):
            return fail("Cannot restore while backup is running.")

        world_dir, props_path, props_text, old_level_name, error_message = _load_restore_context(ctx)
        if error_message:
            return fail(error_message)

        restore_state.was_active = ctx.get_status() == "active"
        if restore_state.was_active and not stop_service_systemd(ctx):
            return fail("Could not stop service for restore.")
        if restore_state.was_active:
            restore_state.service_stopped_for_restore = True
            progress("Minecraft service stopped for restore.")

        extract_root, restore_source, failure = _prepare_restore_source(
            ctx,
            selected_name,
            source_entry,
            restore_state,
            progress,
        )
        if failure is not None:
            return failure

        stored_id, active_id, stored_world_name, restore_base_name = _reserve_restore_names(
            ctx,
            old_level_name,
            restore_source_name,
            restore_source,
        )
        pre_restore_snapshot, failure = _create_snapshot_for_restore(
            ctx,
            world_dir,
            stored_world_name,
            safe_name,
            progress,
            fail,
        )
        if failure is not None:
            return failure

        active_id, new_world_dir = _next_restore_world_dir(
            ctx,
            restore_base_name,
            stored_id,
            active_id,
            world_dir.parent,
        )
        failure = _apply_restore_data(restore_source, new_world_dir, progress, fail)
        if failure is not None:
            return failure

        progress("Archiving previous world directory.")
        archived_old_world_dir, archive_err = _archive_old_world_dir(ctx, world_dir, stored_world_name)
        if archived_old_world_dir is None:
            return fail(archive_err or "Failed to archive previous world directory.")

        failure = _switch_server_properties(
            props_path,
            props_text,
            world_dir,
            archived_old_world_dir,
            new_world_dir,
            fail,
        )
        if failure is not None:
            return failure

        if not _record_restore_history(ctx, safe_name, world_dir, archived_old_world_dir, new_world_dir):
            ctx.log_mcweb_action(
                "restore-backup",
                command=safe_name,
                rejection_message=(
                    f"Restored and switched world, but failed to update restore.history for {world_dir} "
                    f"-> {archived_old_world_dir}."
                ),
            )
        _append_restore_name_run(
            ctx,
            selected_name=selected_name,
            restore_source=restore_source,
            old_level_name=old_level_name,
            stored_world_name=stored_world_name,
            stored_id=stored_id,
            new_world_dir=new_world_dir,
            active_id=active_id,
            pre_restore_snapshot=pre_restore_snapshot,
            archived_old_world_dir=archived_old_world_dir,
        )
        ctx.WORLD_DIR = new_world_dir
        progress(f"server.properties level-name switched to: {new_world_dir.name}")

        restarted, failure = _restart_after_success(ctx, restore_state, progress, fail)
        if failure is not None:
            return failure

        restore_state.restore_succeeded = True
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
        if not restore_state.restore_succeeded:
            _restart_service_after_failure(ctx, restore_state)
        return _restore_failed("Backup zip is invalid or corrupted.")
    except ValueError:
        if not restore_state.restore_succeeded:
            _restart_service_after_failure(ctx, restore_state)
        return _restore_failed("Backup zip contains unsafe paths.")
    except Exception as exc:
        log_exception = getattr(ctx, "log_mcweb_exception", None)
        if log_exception is not None:
            log_exception("restore_world_backup", exc)
        if not restore_state.restore_succeeded:
            _restart_service_after_failure(ctx, restore_state)
        return _restore_failed("Restore failed due to an internal error.")
    finally:
        if extract_root is not None:
            ports.filesystem.rmtree(extract_root, ignore_errors=True)
        ctx.restore_lock.release()


def _restore_status_defaults():
    return {
        "job_id": "",
        "running": False,
        "seq": 0,
        "events": [],
        "result": None,
        "undo_filename": "",
    }


def _ensure_restore_status_state(ctx):
    state = getattr(ctx, "restore_status", None)
    if not isinstance(state, dict):
        state = _restore_status_defaults()
        try:
            setattr(ctx, "restore_status", state)
        except Exception:
            pass
    else:
        for key, value in _restore_status_defaults().items():
            state.setdefault(key, value if not isinstance(value, list) else list(value))

    lock = getattr(ctx, "restore_status_lock", None)
    if not isinstance(lock, threading.Lock):
        lock = threading.Lock()
        try:
            setattr(ctx, "restore_status_lock", lock)
        except Exception:
            pass
    return state, lock


def append_restore_event(ctx, message):
    state, lock = _ensure_restore_status_state(ctx)
    text = str(message or "").strip()
    if not text:
        return None
    with lock:
        state["seq"] = int(state.get("seq", 0)) + 1
        event = {
            "seq": state["seq"],
            "message": text,
            "at": time.time(),
        }
        events = state.setdefault("events", [])
        events.append(event)
        if len(events) > 120:
            del events[:-120]
    return event


def get_restore_status(ctx, since_seq=0, job_id=None):
    state, lock = _ensure_restore_status_state(ctx)
    try:
        since = int(since_seq or 0)
    except (TypeError, ValueError):
        since = 0
    requested_job_id = str(job_id or "").strip()
    with lock:
        current_job_id = str(state.get("job_id", "") or "")
        if requested_job_id and current_job_id and requested_job_id != current_job_id:
            return {
                "ok": True,
                "job_id": requested_job_id,
                "running": False,
                "seq": int(state.get("seq", 0) or 0),
                "events": [],
                "result": None,
                "undo_filename": "",
            }
        events = [dict(item) for item in state.get("events", []) if int(item.get("seq", 0) or 0) > since]
        result = state.get("result")
        return {
            "ok": True,
            "job_id": current_job_id,
            "running": bool(state.get("running")),
            "seq": int(state.get("seq", 0) or 0),
            "events": events,
            "result": dict(result) if isinstance(result, dict) else result,
            "undo_filename": str(state.get("undo_filename", "") or ""),
        }


def _record_restore_run(ctx, job_id, backup_filename, result):
    if not isinstance(result, dict):
        return
    payload = {
        "job_id": str(job_id or ""),
        "mode": "snapshot" if str(backup_filename or "").startswith(SNAPSHOT_TOKEN_PREFIX) else "backup",
        "backup_filename": str(backup_filename or ""),
        "ok": bool(result.get("ok")),
        "error_code": str(result.get("error", "") or ""),
        "message": str(result.get("message", "") or ""),
        "pre_restore_snapshot_name": str(result.get("pre_restore_snapshot_name", "") or ""),
        "switched_from_world": str(result.get("switched_from_world", "") or ""),
        "archived_old_world": str(result.get("archived_old_world", "") or ""),
        "switched_to_world": str(result.get("switched_to_world", "") or ""),
        "stored_restore_id": str(result.get("stored_restore_id", "") or ""),
        "active_restore_id": str(result.get("active_restore_id", "") or ""),
    }
    try:
        state_store_service.append_restore_run(Path(ctx.APP_STATE_DB_PATH), payload)
    except Exception as exc:
        log_exception = getattr(ctx, "log_mcweb_exception", None)
        if log_exception is not None:
            log_exception("append_restore_run", exc)
        return

    if payload["ok"] and payload["pre_restore_snapshot_name"]:
        try:
            state_store_service.restore_backup_records_match(
                Path(ctx.APP_STATE_DB_PATH),
                backup_filename=payload["backup_filename"],
                pre_restore_snapshot_name=payload["pre_restore_snapshot_name"],
                stored_restore_id=payload["stored_restore_id"],
                active_restore_id=payload["active_restore_id"],
            )
        except Exception as exc:
            log_exception = getattr(ctx, "log_mcweb_exception", None)
            if log_exception is not None:
                log_exception("restore_backup_records_match", exc)


def start_restore_job(ctx, backup_filename):
    state, lock = _ensure_restore_status_state(ctx)
    with lock:
        if bool(state.get("running")):
            return {
                "ok": False,
                "error": "restore_in_progress",
                "message": "A restore operation is already in progress.",
                "job_id": str(state.get("job_id", "") or ""),
            }
        job_id = uuid.uuid4().hex[:12]
        state["job_id"] = job_id
        state["running"] = True
        state["result"] = None
        state["undo_filename"] = ""
        state["events"] = []
    append_restore_event(ctx, f"Restore job queued: {str(backup_filename or '').strip()}")

    def _worker():
        result = None
        try:
            result = restore_world_backup(ctx, backup_filename, progress_callback=lambda message: append_restore_event(ctx, message))
        except Exception as exc:
            log_exception = getattr(ctx, "log_mcweb_exception", None)
            if log_exception is not None:
                log_exception("start_restore_job", exc)
            result = {"ok": False, "error": "restore_failed", "message": "Restore failed due to an internal error."}
        finally:
            _record_restore_run(ctx, job_id, backup_filename, result)
            with lock:
                state["running"] = False
                state["result"] = dict(result) if isinstance(result, dict) else result
                state["undo_filename"] = str((result or {}).get("pre_restore_snapshot_name", "") or "")
            append_restore_event(ctx, str((result or {}).get("message", "Restore completed.")))

    try:
        start_detached(target=_worker, daemon=True)
    except Exception as exc:
        log_exception = getattr(ctx, "log_mcweb_exception", None)
        if log_exception is not None:
            log_exception("start_restore_job/thread", exc)
        with lock:
            state["running"] = False
            state["result"] = {
                "ok": False,
                "error": "thread_start_failed",
                "message": "Failed to start restore worker thread.",
            }
        append_restore_event(ctx, "Failed to start restore worker thread.")
        return {"ok": False, "error": "thread_start_failed", "message": "Failed to start restore worker thread.", "job_id": job_id}
    return {"ok": True, "job_id": job_id}


def start_restore_worker(ctx, backup_filename):
    return start_restore_job(ctx, backup_filename)
