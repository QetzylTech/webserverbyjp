"""Background worker loops for split-role deployments."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, cast

from app.core import state_store as state_store_service
from app.services import dashboard_file_runtime as dashboard_file_runtime_service
from app.services import file_inventory_index as file_inventory_index_service
from app.services.maintenance_engine import _cleanup_evaluate
from app.services.maintenance_snapshot import _cleanup_state_snapshot
from app.services.maintenance_state_store import (
    _cleanup_data_dir,
    _cleanup_get_scope_view,
    _cleanup_load_config,
)
from app.services.worker_scheduler import WorkerSpec, start_worker

_state_store_service = cast(Any, state_store_service)
_dashboard_file_runtime_service = cast(Any, dashboard_file_runtime_service)
_file_inventory_index_service = cast(Any, file_inventory_index_service)


def _event_id(value: object, default: int = 0) -> int:
    try:
        return int(str(value or default))
    except Exception:
        return default


def _update_operation(ctx: Any, op_id: str, **updates: Any) -> None:
    _state_store_service.update_operation(ctx.APP_STATE_DB_PATH, op_id=op_id, **updates)


def _mark_operation_started(ctx: Any, op_id: str, message: str) -> None:
    _update_operation(
        ctx,
        op_id,
        status="in_progress",
        checkpoint="worker_started",
        started=True,
        message=message,
    )


def _fail_operation(
    ctx: Any,
    op_id: str,
    *,
    error_code: str,
    checkpoint: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    updates = {
        "status": "failed",
        "error_code": error_code,
        "checkpoint": checkpoint,
        "message": message,
        "finished": True,
    }
    if payload is not None:
        updates["payload"] = payload
    _update_operation(ctx, op_id, **updates)


def _complete_operation(
    ctx: Any,
    op_id: str,
    *,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    updates = {
        "status": "observed",
        "checkpoint": "observed",
        "message": message,
        "finished": True,
    }
    if payload is not None:
        updates["payload"] = payload
    _update_operation(ctx, op_id, **updates)


def _dispatch_control_intent(ctx: Any, op_type: str, op_id: str, target: object) -> None:
    handlers: dict[str, Callable[[], None]] = {
        "start": lambda: _execute_start(ctx, op_id),
        "stop": lambda: _execute_stop(ctx, op_id),
        "backup": lambda: _execute_backup(ctx, op_id),
        "restore": lambda: _execute_restore(ctx, op_id, target),
    }
    handler = handlers.get(op_type)
    if handler is None:
        return
    handler()


def _interval_seconds(ctx: Any, name: str, default_value: float) -> float:
    try:
        value = float(getattr(ctx, name, default_value))
    except Exception:
        value = float(default_value)
    return max(1.0, value)


def _maintenance_precompute_loop(ctx: Any) -> None:
    interval = _interval_seconds(ctx, "WORKER_MAINTENANCE_PRECOMPUTE_INTERVAL_SECONDS", 8.0)
    while True:
        try:
            full_cfg = _cleanup_load_config(ctx)
            for scope in ("backups", "stale_worlds"):
                cfg = _cleanup_get_scope_view(full_cfg, scope)
                snapshot = _cleanup_state_snapshot(ctx, cfg)
                preview = _cleanup_evaluate(ctx, cfg, mode="rule", apply_changes=False, trigger="worker_precompute")
                _state_store_service.append_event(
                    ctx.APP_STATE_DB_PATH,
                    topic=f"maintenance_state:{scope}",
                    payload={
                        "scope": scope,
                        "snapshot": snapshot if isinstance(snapshot, dict) else {},
                        "preview": preview if isinstance(preview, dict) else {},
                    },
                )
        except Exception as exc:
            ctx.log_mcweb_exception("worker_maintenance_precompute_loop", exc)
        time.sleep(interval)


def _execute_start(ctx: Any, op_id: str) -> None:
    _mark_operation_started(ctx, op_id, "Start operation in progress.")
    result = ctx.start_service_non_blocking(timeout=12)
    if not bool((result or {}).get("ok")):
        message = str((result or {}).get("message", "Failed to start service.") or "Failed to start service.")
        ctx.set_service_status_intent(None)
        ctx.invalidate_status_cache()
        _fail_operation(
            ctx,
            op_id,
            error_code="start_failed",
            checkpoint="start_failed",
            message=message,
        )
        return
    if ctx.write_session_start_time() is None:
        try:
            ctx.log_mcweb_log(
                "start-session-warning",
                command="write_session_start_time",
                rejection_message="Session file write failed; continuing startup tracking via operation state.",
            )
        except Exception:
            pass
    ctx.reset_backup_schedule_state()
    # Do not mark observed yet: startup completion is reconciled from live
    # runtime status to avoid flipping back to Off during warm-up.
    _update_operation(
        ctx,
        op_id,
        status="in_progress",
        checkpoint="start_dispatched",
        message="Start dispatched; awaiting observed active state.",
        finished=False,
    )


def _execute_stop(ctx: Any, op_id: str) -> None:
    _mark_operation_started(ctx, op_id, "Stop operation in progress.")
    result = ctx.graceful_stop_minecraft()
    service_stop_ok = bool((result or {}).get("service_stop_ok")) if isinstance(result, dict) else bool(result)
    backup_ok = bool((result or {}).get("backup_ok")) if isinstance(result, dict) else True
    if not (service_stop_ok and backup_ok):
        message = "Stop operation failed."
        if isinstance(result, dict):
            if not service_stop_ok:
                message = "Stop operation failed: service did not stop cleanly."
            elif not backup_ok:
                message = "Stop operation failed: backup pre-stop hook failed."
        ctx.set_service_status_intent(None)
        ctx.invalidate_status_cache()
        _fail_operation(
            ctx,
            op_id,
            error_code="stop_failed",
            checkpoint="stop_failed",
            message=message,
        )
        return
    ctx.clear_session_start_time()
    ctx.reset_backup_schedule_state()
    _complete_operation(ctx, op_id, message="Service stop observed.")


def _execute_backup(ctx: Any, op_id: str) -> None:
    _mark_operation_started(ctx, op_id, "Backup operation in progress.")
    ok = ctx.run_backup_script(trigger="manual")
    if not ok:
        detail = ""
        with ctx.backup_state.lock:
            detail = str(ctx.backup_state.last_error or "")
        message = f"Backup failed: {detail}" if detail else "Backup failed."
        _fail_operation(
            ctx,
            op_id,
            error_code="backup_failed",
            checkpoint="backup_failed",
            message=message,
        )
        return
    _complete_operation(ctx, op_id, message="Backup operation observed complete.")


def _execute_restore(ctx: Any, op_id: str, target: object) -> None:
    _mark_operation_started(ctx, op_id, "Restore operation in progress.")
    filename = str(target or "").strip()
    result = ctx.start_restore_job(filename)
    if not bool((result or {}).get("ok")):
        message = str((result or {}).get("message", "Restore failed to start.") or "Restore failed to start.")
        error_code = str((result or {}).get("error", "restore_start_failed") or "restore_start_failed")
        _fail_operation(
            ctx,
            op_id,
            error_code=error_code,
            checkpoint="restore_start_failed",
            message=message,
        )
        return

    restore_job_id = str((result or {}).get("job_id", "") or "")
    restore_log_file = str((result or {}).get("log_file", "") or "")
    _update_operation(
        ctx,
        op_id,
        status="in_progress",
        checkpoint="restore_job_started",
        message="Restore worker started.",
        payload={"restore_job_id": restore_job_id, "restore_log_file": restore_log_file},
    )

    deadline = time.time() + (2 * 60 * 60)
    last_payload = {}
    while time.time() < deadline:
        payload = ctx.get_restore_status(since_seq=0, job_id=restore_job_id)
        last_payload = payload if isinstance(payload, dict) else {}
        if not bool(last_payload.get("running")):
            break
        time.sleep(0.4)

    result_payload = last_payload.get("result") if isinstance(last_payload, dict) else None
    if isinstance(result_payload, dict) and bool(result_payload.get("ok")):
        _complete_operation(
            ctx,
            op_id,
            message=str(result_payload.get("message", "Restore completed successfully.") or "Restore completed successfully."),
            payload={"restore_job_id": restore_job_id, "result": result_payload},
        )
        return
    message = "Restore failed."
    error_code = "restore_failed"
    if isinstance(result_payload, dict):
        message = str(result_payload.get("message", message) or message)
        error_code = str(result_payload.get("error", error_code) or error_code)
    _fail_operation(
        ctx,
        op_id,
        error_code=error_code,
        checkpoint="restore_failed",
        message=message,
        payload={"restore_job_id": restore_job_id, "result": result_payload if isinstance(result_payload, dict) else {}},
    )


def _control_intent_consumer_loop(ctx: Any) -> None:
    interval = _interval_seconds(ctx, "WORKER_CONTROL_INTENT_POLL_SECONDS", 0.75)
    last_id = 0
    while True:
        try:
            rows = _state_store_service.list_events_since(
                ctx.APP_STATE_DB_PATH,
                topic="control_intent",
                since_id=last_id,
                limit=50,
            )
        except Exception as exc:
            ctx.log_mcweb_exception("worker_control_intent_list", exc)
            rows = []
        for row in rows:
            event_id = _event_id((row or {}).get("id", 0))
            if event_id > last_id:
                last_id = event_id
            payload = row.get("payload", {}) if isinstance(row, dict) else {}
            if not isinstance(payload, dict):
                continue
            op_id = str(payload.get("op_id", "") or "").strip()
            op_type = str(payload.get("op_type", "") or "").strip().lower()
            target = str(payload.get("target", "") or "")
            if not op_id or op_type not in {"start", "stop", "backup", "restore"}:
                continue
            try:
                item = _state_store_service.get_operation(ctx.APP_STATE_DB_PATH, op_id)
            except Exception:
                item = None
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "") or "").strip().lower()
            if status != "intent":
                continue
            try:
                _dispatch_control_intent(ctx, op_type, op_id, target)
            except Exception as exc:
                ctx.log_mcweb_exception(f"worker_execute_{op_type}", exc)
                try:
                    _fail_operation(
                        ctx,
                        op_id,
                        error_code=f"{op_type}_worker_failed",
                        checkpoint=f"{op_type}_worker_failed",
                        message=str(exc)[:700],
                    )
                except Exception:
                    pass
        time.sleep(interval)


def _index_refresh_loop(ctx: Any) -> None:
    interval = _interval_seconds(ctx, "WORKER_INDEX_REFRESH_INTERVAL_SECONDS", 6.0)
    while True:
        try:
            backup_dir = Path(ctx.BACKUP_DIR)
            snapshot_root = Path(getattr(ctx, "AUTO_SNAPSHOT_DIR", "") or (backup_dir / "snapshots"))
            old_worlds_root = (_cleanup_data_dir(ctx) / "old_worlds").resolve()
            _file_inventory_index_service.get_inventory(
                backup_root=backup_dir,
                snapshot_root=snapshot_root,
                old_worlds_root=old_worlds_root,
            )
            _dashboard_file_runtime_service.refresh_file_page_items(ctx, "backups")
        except Exception as exc:
            ctx.log_mcweb_exception("worker_index_refresh_loop", exc)
        time.sleep(interval)


def start_worker_loops(ctx: Any) -> None:
    """Start worker-only loops: reconciler + maintenance precompute + index refresh."""
    try:
        ctx.start_operation_reconciler()
    except Exception as exc:
        ctx.log_mcweb_exception("worker_start_operation_reconciler", exc)
    for source in tuple(getattr(ctx, "LOG_SOURCE_KEYS", ())):
        try:
            ctx.ensure_log_stream_fetcher_started(source)
        except Exception as exc:
            ctx.log_mcweb_exception(f"worker_start_log_fetcher/{source}", exc)
    try:
        ctx.ensure_metrics_collector_started()
    except Exception as exc:
        ctx.log_mcweb_exception("worker_start_metrics_collector", exc)
    for name, starter in (
        ("worker_start_idle_player_watcher", getattr(ctx, "start_idle_player_watcher", None)),
        ("worker_start_backup_session_watcher", getattr(ctx, "start_backup_session_watcher", None)),
        ("worker_start_storage_safety_watcher", getattr(ctx, "start_storage_safety_watcher", None)),
    ):
        if not callable(starter):
            continue
        try:
            starter()
        except Exception as exc:
            ctx.log_mcweb_exception(name, exc)
    start_worker(
        ctx,
        WorkerSpec(
            name="worker-control-intent-consumer",
            target=_control_intent_consumer_loop,
            args=(ctx,),
            interval_source=getattr(ctx, "WORKER_CONTROL_INTENT_POLL_SECONDS", None),
            stop_signal_name="worker_control_intent_stop_event",
            health_marker="worker_control_intent_consumer",
        ),
    )
    start_worker(
        ctx,
        WorkerSpec(
            name="worker-maintenance-precompute",
            target=_maintenance_precompute_loop,
            args=(ctx,),
            interval_source=getattr(ctx, "WORKER_MAINTENANCE_PRECOMPUTE_INTERVAL_SECONDS", None),
            stop_signal_name="worker_maintenance_precompute_stop_event",
            health_marker="worker_maintenance_precompute",
        ),
    )
    start_worker(
        ctx,
        WorkerSpec(
            name="worker-index-refresh",
            target=_index_refresh_loop,
            args=(ctx,),
            interval_source=getattr(ctx, "WORKER_INDEX_REFRESH_INTERVAL_SECONDS", None),
            stop_signal_name="worker_index_refresh_stop_event",
            health_marker="worker_index_refresh",
        ),
    )
