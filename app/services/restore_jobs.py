"""Restore job orchestration for control plane."""

from pathlib import Path
import uuid
from typing import Any

from app.core import state_store as state_store_service
from app.services.restore_execution import SNAPSHOT_TOKEN_PREFIX, restore_world_backup
from app.services.restore_log_utils import build_restore_log_filename
from app.services.restore_status import _ensure_restore_status_state, append_restore_event
from app.services.worker_scheduler import start_detached


def _resolve_restore_log_dir(ctx: Any) -> Path:
    log_dir = getattr(ctx, "MCWEB_LOG_DIR", None)
    if log_dir:
        return log_dir if isinstance(log_dir, Path) else Path(str(log_dir))
    log_file = getattr(ctx, "MCWEB_LOG_FILE")
    return log_file.parent if isinstance(log_file, Path) else Path(str(log_file)).parent


def _record_restore_run(ctx: Any, job_id: object, backup_filename: object, result: object) -> None:
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


def start_restore_job(ctx: Any, backup_filename: object) -> dict[str, object]:
    state, lock = _ensure_restore_status_state(ctx)
    log_file_name = ""
    with lock:
        if bool(state.get("running")):
            return {
                "ok": False,
                "error": "restore_in_progress",
                "message": "A restore operation is already in progress.",
                "job_id": str(state.get("job_id", "") or ""),
            }
        job_id = uuid.uuid4().hex[:12]
        try:
            log_name = build_restore_log_filename(
                str(backup_filename or ""),
                job_id,
                getattr(ctx, "DISPLAY_TZ", None),
            )
            log_dir = _resolve_restore_log_dir(ctx)
            state["log_file"] = str(log_dir / log_name)
            log_file_name = str(log_name)
        except Exception:
            state["log_file"] = None
            log_file_name = ""
        state["job_id"] = job_id
        state["running"] = True
        state["result"] = None
        state["events"] = []
    append_restore_event(ctx, f"Restore job queued: {str(backup_filename or '').strip()}")

    def _worker() -> None:
        result: dict[str, object] | None = None

        def _progress(message: str) -> None:
            append_restore_event(ctx, message)

        try:
            result = restore_world_backup(
                ctx,
                str(backup_filename or ""),
                progress_callback=_progress,
            )
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
        return {"ok": False, "error": "thread_start_failed", "message": "Failed to start restore worker thread.", "job_id": job_id, "log_file": log_file_name}
    return {"ok": True, "job_id": job_id, "log_file": log_file_name}


start_restore_worker = start_restore_job
