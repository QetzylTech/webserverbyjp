"""Dashboard operation reconciliation and consistency checks."""
from datetime import datetime
from pathlib import Path
import time

from app.core import profiling
from app.core import state_store as state_store_service
from app.services.worker_scheduler import WorkerSpec, start_worker


def _operation_age_seconds(op, now_epoch):
    if not isinstance(op, dict):
        return 0.0
    started = str(op.get("started_at", "") or "").strip()
    intent = str(op.get("intent_at", "") or "").strip()
    source = started or intent
    if not source:
        return 0.0
    try:
        ts = datetime.fromisoformat(source.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0
    return max(0.0, now_epoch - ts)


def get_consistency_report(ctx, *, auto_repair=False):
    """Validate runtime invariants and optionally repair safe drift."""
    with profiling.timed("consistency.report"):
        issues = []
        repairs = []
        service_status = str(ctx.get_status() or "").strip().lower()
        try:
            session_start = ctx.read_session_start_time()
        except Exception:
            session_start = None

        if service_status not in ctx.OFF_STATES and session_start is None:
            issue = {
                "code": "active_missing_session_start",
                "message": "Service is active but session start timestamp is missing.",
                "severity": "warning",
            }
            issues.append(issue)
            if auto_repair:
                try:
                    repaired = ctx.write_session_start_time() is not None
                except Exception:
                    repaired = False
                repairs.append({
                    "code": "write_session_start",
                    "ok": bool(repaired),
                    "message": "Attempted to restore missing session timestamp.",
                })

        if service_status in ctx.OFF_STATES and session_start is not None:
            issue = {
                "code": "off_with_session_start",
                "message": "Service is off but session start timestamp still exists.",
                "severity": "warning",
            }
            issues.append(issue)
            if auto_repair:
                try:
                    ctx.clear_session_start_time()
                    repaired = True
                except Exception:
                    repaired = False
                repairs.append({
                    "code": "clear_session_start",
                    "ok": bool(repaired),
                    "message": "Attempted to clear stale session timestamp.",
                })

        return {
            "ok": len(issues) == 0,
            "service_status_raw": service_status,
            "issues": issues,
            "repairs": repairs,
            "checked_at": datetime.now().isoformat(),
        }


def reconcile_operations_once(ctx):
    """Advance stale/finished async operations using observed runtime state."""
    with profiling.timed("reconciler.iteration"):
        db_path = Path(ctx.APP_STATE_DB_PATH)
        with profiling.timed("reconciler.fetch_active_ops"):
            active_ops = state_store_service.list_operations_by_status(
                db_path,
                statuses=("intent", "in_progress"),
                limit=200,
            )
        profiling.set_gauge("reconciler.active_ops", len(active_ops))
        if not active_ops:
            with profiling.timed("reconciler.consistency_check"):
                try:
                    get_consistency_report(ctx, auto_repair=True)
                except Exception as exc:
                    ctx.log_mcweb_exception("reconcile_consistency_report", exc)
            return 0
        updated = 0
        now_epoch = time.time()
        service_status = str(ctx.get_status() or "").strip().lower()
        pending_updates = []

        def _queue_update(op_id, **kwargs):
            if not str(op_id or "").strip():
                return
            payload = {"op_id": str(op_id)}
            payload.update(kwargs)
            pending_updates.append(payload)

        for op in active_ops:
            with profiling.timed("reconciler.per_operation"):
                op_id = str(op.get("op_id", "") or "")
                op_type = str(op.get("op_type", "") or "").strip().lower()
                status = str(op.get("status", "") or "").strip().lower()
                age = _operation_age_seconds(op, now_epoch)
                data = op.get("data", {}) if isinstance(op.get("data"), dict) else {}

                if op_type == "start":
                    if service_status == "active":
                        _queue_update(
                            op_id,
                            status="observed",
                            message="Service start observed by reconciler.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if status == "intent" and age >= float(ctx.OPERATION_INTENT_STALE_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="intent_stale",
                            message="Start operation stale before worker progress.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if age >= float(ctx.OPERATION_START_TIMEOUT_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="start_timeout",
                            message="Start operation timed out.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    continue

                if op_type == "restore":
                    restore_job_id = str(data.get("restore_job_id", "") or "").strip()
                    if restore_job_id:
                        payload = ctx.get_restore_status(since_seq=0, job_id=restore_job_id)
                        if isinstance(payload, dict) and not bool(payload.get("running")):
                            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                            if bool(result.get("ok")):
                                _queue_update(
                                    op_id,
                                    status="observed",
                                    message=str(result.get("message", "Restore observed complete.") or "Restore observed complete."),
                                    payload={"restore_job_id": restore_job_id, "result": result},
                                    finished=True,
                                )
                            else:
                                _queue_update(
                                    op_id,
                                    status="failed",
                                    error_code=str(result.get("error", "") or "restore_failed"),
                                    message=str(result.get("message", "Restore failed.") or "Restore failed."),
                                    payload={"restore_job_id": restore_job_id, "result": result},
                                    finished=True,
                                )
                            updated += 1
                            continue

                    if status == "intent" and age >= float(ctx.OPERATION_INTENT_STALE_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="intent_stale",
                            message="Restore operation stale before worker progress.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if age >= float(ctx.OPERATION_RESTORE_TIMEOUT_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="restore_timeout",
                            message="Restore operation timed out.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    continue

                if op_type == "stop":
                    if service_status in ctx.OFF_STATES:
                        _queue_update(
                            op_id,
                            status="observed",
                            message="Service stop observed by reconciler.",
                            finished=True,
                        )
                        intent_setter = getattr(ctx, "set_service_status_intent", None)
                        if callable(intent_setter):
                            try:
                                intent_setter(None)
                            except Exception:
                                pass
                        updated += 1
                        continue
                    if status == "intent" and age >= float(ctx.OPERATION_INTENT_STALE_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="intent_stale",
                            message="Stop operation stale before worker progress.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if age >= float(ctx.OPERATION_STOP_TIMEOUT_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="stop_timeout",
                            message="Stop operation timed out.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    continue

                if op_type == "backup":
                    if status == "intent" and age >= float(ctx.OPERATION_INTENT_STALE_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="intent_stale",
                            message="Backup operation stale before worker progress.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if age >= float(ctx.OPERATION_BACKUP_TIMEOUT_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="backup_timeout",
                            message="Backup operation timed out.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    continue

                if op_type == "rcon":
                    if status == "intent" and age >= float(ctx.OPERATION_INTENT_STALE_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="intent_stale",
                            message="RCON operation stale before worker progress.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if age >= float(ctx.OPERATION_RCON_TIMEOUT_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="rcon_timeout",
                            message="RCON operation timed out.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    continue

        for payload in pending_updates:
            try:
                state_store_service.update_operation(db_path, **payload)
            except Exception as exc:
                ctx.log_mcweb_exception("reconcile_operation_update", exc)

        if updated:
            try:
                consistency = get_consistency_report(ctx, auto_repair=True)
                if not consistency.get("ok"):
                    ctx.log_mcweb_log(
                        "reconciler-consistency",
                        command="get_consistency_report",
                        rejection_message=str(consistency.get("issues", ""))[:700],
                    )
            except Exception as exc:
                ctx.log_mcweb_exception("reconcile_consistency_report_post", exc)
        return updated


def operation_reconciler_loop(ctx):
    """Periodic reconciler to mark operations observed/stale."""
    while True:
        try:
            reconcile_operations_once(ctx)
        except Exception as exc:
            ctx.log_mcweb_exception("operation_reconciler_loop", exc)
        time.sleep(float(ctx.OPERATION_RECONCILE_INTERVAL_SECONDS))


def start_operation_reconciler(ctx):
    """Start the operation reconciler daemon once."""
    if ctx.operation_reconciler_started:
        return
    with ctx.operation_reconciler_start_lock:
        if ctx.operation_reconciler_started:
            return
        start_worker(
            ctx,
            WorkerSpec(
                name="operation-reconciler",
                target=operation_reconciler_loop,
                args=(ctx,),
                interval_source=getattr(ctx, "OPERATION_RECONCILE_INTERVAL_SECONDS", None),
                stop_signal_name="operation_reconciler_stop_event",
                health_marker="operation_reconciler",
            ),
        )
        ctx.operation_reconciler_started = True
