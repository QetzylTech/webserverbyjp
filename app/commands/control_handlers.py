"""Command handlers for control-plane start/stop/backup/restore actions."""
from __future__ import annotations

import time

from app.core import state_store as state_store_service
from app.commands.control_support import (
    _accepted_operation_result,
    _enqueue_control_intent,
    _invalidate_observed_cache,
    _payload_result,
    _prepare_operation,
    _refresh_runtime_status,
    _response_result,
    _start_operation_worker,
    _update_operation_record,
    enforce_rate_limit,
)


def start_operation(ctx, *, idempotency_key, client_key):
    state = ctx.state
    limited = enforce_rate_limit(ctx, "start", client_key=client_key, limit=8, window_seconds=30.0)
    if limited is not None:
        return limited
    if state["is_storage_low"]():
        message = state["low_storage_error_message"]()
        state["log_mcweb_action"]("start", rejection_message=message)
        return _response_result(state["_low_storage_blocked_response"](message))

    op_id, resumed, result = _prepare_operation(
        ctx,
        "start",
        target=state.get("SERVICE", "minecraft"),
        payload={},
        idempotency_key=idempotency_key,
        active_log_action="start",
        log_action="start",
        load_error_result=_response_result(state["_start_failed_response"]("Failed to load start operation record.")),
        resume_error_result=_response_result(state["_start_failed_response"]("Failed to resume start operation.")),
        create_error_result=_response_result(state["_start_failed_response"]("Failed to create start operation record.")),
    )
    if result is not None:
        return result

    _enqueue_control_intent(ctx, "start", op_id, target=state.get("SERVICE", "minecraft"))
    _refresh_runtime_status(ctx, "starting", invalidate_observed=True)

    def _start_worker():
        _update_operation_record(
            ctx,
            op_id,
            "start_in_progress",
            status="in_progress",
            checkpoint="worker_started",
            started=True,
            message="Start operation in progress.",
        )
        result = state["start_service_non_blocking"](timeout=12)
        if not result.get("ok"):
            message = result.get("message", "Failed to start service.")
            _refresh_runtime_status(ctx, None)
            state["log_mcweb_action"]("start-worker", rejection_message=message)
            _update_operation_record(
                ctx,
                op_id,
                "start_failed",
                status="failed",
                error_code="start_failed",
                checkpoint="start_failed",
                message=message,
                finished=True,
            )
            return
        if state["write_session_start_time"]() is None:
            try:
                state["log_mcweb_action"](
                    "start-worker",
                    rejection_message="Session file write failed; continuing startup tracking.",
                )
            except Exception:
                pass
        state["reset_backup_schedule_state"]()
        _update_operation_record(
            ctx,
            op_id,
            "start_observed",
            status="in_progress",
            checkpoint="start_dispatched",
            message="Start dispatched; awaiting observed active state.",
            finished=False,
        )
        _refresh_runtime_status(ctx, "starting", invalidate_observed=True)

    worker_result = _start_operation_worker(
        ctx,
        "start",
        op_id,
        target=_start_worker,
        thread_error_message="Failed to start service worker thread.",
        error_result_builder=lambda message: _response_result(state["_start_failed_response"](message)),
        on_thread_start_failed=lambda: (
            _refresh_runtime_status(ctx, None),
            state["log_mcweb_action"]("start-worker", rejection_message="Failed to start service worker thread."),
        ),
    )
    if worker_result is not None:
        return worker_result

    state["log_mcweb_action"]("start")
    return _accepted_operation_result(op_id, existing=resumed, resumed=resumed)


def stop_operation(ctx, *, idempotency_key, client_key, sudo_password):
    state = ctx.state
    limited = enforce_rate_limit(ctx, "stop", client_key=client_key, limit=8, window_seconds=30.0)
    if limited is not None:
        return limited
    if not state["validate_sudo_password"](sudo_password):
        state["log_mcweb_action"]("stop", rejection_message="Password incorrect.")
        return _response_result(state["_password_rejected_response"]())
    state["record_successful_password_ip"]()

    op_id, resumed, result = _prepare_operation(
        ctx,
        "stop",
        target=state.get("SERVICE", "minecraft"),
        payload={},
        idempotency_key=idempotency_key,
        active_log_action="stop",
        log_action="stop",
        load_error_result=_response_result(state["_start_failed_response"]("Failed to load stop operation record.")),
        resume_error_result=_response_result(state["_start_failed_response"]("Failed to resume stop operation.")),
        create_error_result=_response_result(state["_start_failed_response"]("Failed to create stop operation record.")),
    )
    if result is not None:
        return result

    _enqueue_control_intent(ctx, "stop", op_id, target=state.get("SERVICE", "minecraft"))
    _refresh_runtime_status(ctx, "shutting", invalidate_observed=True)

    if ctx.process_role == "web":
        state["log_mcweb_action"]("stop")
        return _accepted_operation_result(op_id, existing=resumed, resumed=resumed, queued=True)

    def _stop_worker():
        _update_operation_record(
            ctx,
            op_id,
            "stop_in_progress",
            status="in_progress",
            checkpoint="worker_started",
            started=True,
            message="Stop operation in progress.",
        )
        result = state["graceful_stop_minecraft"]()
        systemd_ok = bool((result or {}).get("systemd_ok")) if isinstance(result, dict) else bool(result)
        backup_ok = bool((result or {}).get("backup_ok")) if isinstance(result, dict) else True
        if not (systemd_ok and backup_ok):
            message = "Stop operation failed."
            if isinstance(result, dict):
                if not systemd_ok:
                    message = "Stop operation failed: service did not stop cleanly."
                elif not backup_ok:
                    message = "Stop operation failed: backup pre-stop hook failed."
            _refresh_runtime_status(ctx, None)
            state["log_mcweb_action"]("stop-worker", rejection_message=message)
            _update_operation_record(
                ctx,
                op_id,
                "stop_failed",
                status="failed",
                error_code="stop_failed",
                checkpoint="stop_failed",
                message=message,
                finished=True,
            )
            return

        state["clear_session_start_time"]()
        state["reset_backup_schedule_state"]()
        ctx.run_cleanup_event_if_enabled(getattr(state, "ctx", state), "server_shutdown")
        _update_operation_record(
            ctx,
            op_id,
            "stop_observed",
            status="observed",
            checkpoint="observed",
            message="Service stop observed.",
            finished=True,
        )
        _refresh_runtime_status(ctx, None, invalidate_observed=True)

    worker_result = _start_operation_worker(
        ctx,
        "stop",
        op_id,
        target=_stop_worker,
        thread_error_message="Failed to start stop worker thread.",
        error_result_builder=lambda message: _response_result(state["_start_failed_response"](message)),
    )
    if worker_result is not None:
        return worker_result

    state["log_mcweb_action"]("stop")
    return _accepted_operation_result(op_id, existing=resumed, resumed=resumed)


def backup_operation(ctx, *, idempotency_key, client_key):
    state = ctx.state
    limited = enforce_rate_limit(ctx, "backup", client_key=client_key, limit=8, window_seconds=30.0)
    if limited is not None:
        return limited

    op_id, resumed, result = _prepare_operation(
        ctx,
        "backup",
        target="manual",
        payload={"trigger": "manual"},
        idempotency_key=idempotency_key,
        active_log_action="backup",
        log_action="backup",
        load_error_result=_response_result(state["_backup_failed_response"]("Failed to load backup operation record.")),
        resume_error_result=_response_result(state["_backup_failed_response"]("Failed to resume backup operation.")),
        create_error_result=_response_result(state["_backup_failed_response"]("Failed to create backup operation record.")),
    )
    if result is not None:
        return result

    _enqueue_control_intent(ctx, "backup", op_id, target="manual")
    _invalidate_observed_cache(ctx)

    def _backup_worker():
        _update_operation_record(
            ctx,
            op_id,
            "backup_in_progress",
            status="in_progress",
            checkpoint="worker_started",
            started=True,
            message="Backup operation in progress.",
        )
        ok = state["run_backup_script"](trigger="manual")
        if not ok:
            detail = ""
            backup_state = state["backup_state"]
            with backup_state.lock:
                detail = backup_state.last_error
            message = "Backup failed."
            if detail:
                message = f"Backup failed: {detail}"
            state["log_mcweb_action"]("backup", rejection_message=message)
            _update_operation_record(
                ctx,
                op_id,
                "backup_failed",
                status="failed",
                error_code="backup_failed",
                checkpoint="backup_failed",
                message=message,
                finished=True,
            )
            return
        _update_operation_record(
            ctx,
            op_id,
            "backup_observed",
            status="observed",
            checkpoint="observed",
            message="Backup operation observed complete.",
            finished=True,
        )
        _invalidate_observed_cache(ctx)

    worker_result = _start_operation_worker(
        ctx,
        "backup",
        op_id,
        target=_backup_worker,
        thread_error_message="Failed to start backup worker thread.",
        error_result_builder=lambda message: _response_result(state["_backup_failed_response"](message)),
    )
    if worker_result is not None:
        return worker_result

    state["log_mcweb_action"]("backup")
    return _accepted_operation_result(op_id, existing=resumed, resumed=resumed)


def restore_operation(ctx, *, idempotency_key, client_key, sudo_password, filename):
    state = ctx.state
    limited = enforce_rate_limit(ctx, "restore-backup", client_key=client_key, limit=6, window_seconds=30.0)
    if limited is not None:
        return limited
    if not state["validate_sudo_password"](sudo_password):
        state["log_mcweb_action"]("restore-backup", command=filename, rejection_message="Password incorrect.")
        return _response_result(state["_password_rejected_response"]())
    state["record_successful_password_ip"]()
    if not filename:
        return _payload_result(
            {"ok": False, "error": "restore_failed", "message": "Backup filename is required."},
            status_code=400,
        )

    op_id, resumed, result = _prepare_operation(
        ctx,
        "restore",
        target=filename,
        payload={},
        idempotency_key=idempotency_key,
        active_target=filename,
        active_message="Restore accepted.",
        active_conflict_result=_payload_result(
            {
                "ok": False,
                "error": "restore_in_progress",
                "message": "Another restore operation is already in progress.",
            },
            status_code=409,
        ),
        accepted_message="Restore accepted.",
        load_error_result=_payload_result(
            {"ok": False, "error": "restore_failed", "message": "Failed to load restore operation record."},
            status_code=500,
        ),
        resume_error_result=_payload_result(
            {"ok": False, "error": "restore_failed", "message": "Failed to resume restore operation."},
            status_code=500,
        ),
        create_error_result=_payload_result(
            {"ok": False, "error": "restore_failed", "message": "Failed to create restore operation record."},
            status_code=500,
        ),
        target_conflict_result=_payload_result(
            {
                "ok": False,
                "error": "idempotency_key_conflict",
                "message": "Idempotency key already used for a different restore target.",
            },
            status_code=409,
        ),
    )
    if result is not None:
        return result

    _enqueue_control_intent(ctx, "restore", op_id, target=filename)
    _invalidate_observed_cache(ctx)

    if ctx.process_role == "web":
        return _accepted_operation_result(
            op_id,
            existing=resumed,
            resumed=resumed,
            queued=True,
            message="Restore accepted.",
        )

    def _restore_worker():
        _update_operation_record(
            ctx,
            op_id,
            "restore_in_progress",
            status="in_progress",
            checkpoint="worker_started",
            started=True,
            message="Restore operation in progress.",
        )
        result = state["start_restore_job"](filename)
        if not result.get("ok"):
            message = result.get("message", "Restore failed to start.")
            state["log_mcweb_action"]("restore-backup", command=filename, rejection_message=message)
            _update_operation_record(
                ctx,
                op_id,
                "restore_failed",
                status="failed",
                error_code=str(result.get("error", "") or "restore_start_failed"),
                checkpoint="restore_start_failed",
                message=message,
                finished=True,
            )
            return

        restore_job_id = str(result.get("job_id", "") or "")
        _update_operation_record(
            ctx,
            op_id,
            "restore_job_started",
            status="in_progress",
            checkpoint="restore_job_started",
            message="Restore worker started.",
            payload={"restore_job_id": restore_job_id},
        )

        deadline = time.time() + (2 * 60 * 60)
        last_payload = {}
        while time.time() < deadline:
            payload = state["get_restore_status"](since_seq=0, job_id=restore_job_id)
            last_payload = payload if isinstance(payload, dict) else {}
            if not last_payload.get("running"):
                break
            time.sleep(0.4)

        result_payload = last_payload.get("result") if isinstance(last_payload, dict) else None
        if isinstance(result_payload, dict) and bool(result_payload.get("ok")):
            _update_operation_record(
                ctx,
                op_id,
                "restore_observed",
                status="observed",
                checkpoint="observed",
                message=str(result_payload.get("message", "Restore completed successfully.") or "Restore completed successfully."),
                payload={"restore_job_id": restore_job_id, "result": result_payload},
                finished=True,
            )
            state["log_mcweb_action"]("restore-backup", command=f"{filename} (started)")
            _invalidate_observed_cache(ctx)
            return

        message = "Restore failed."
        error_code = "restore_failed"
        if isinstance(result_payload, dict):
            message = str(result_payload.get("message", message) or message)
            error_code = str(result_payload.get("error", error_code) or error_code)
        _update_operation_record(
            ctx,
            op_id,
            "restore_terminal_failed",
            status="failed",
            error_code=error_code,
            checkpoint="restore_failed",
            message=message,
            payload={"restore_job_id": restore_job_id, "result": result_payload if isinstance(result_payload, dict) else {}},
            finished=True,
        )
        _invalidate_observed_cache(ctx)

    worker_result = _start_operation_worker(
        ctx,
        "restore",
        op_id,
        target=_restore_worker,
        thread_error_message="Failed to start restore worker thread.",
        error_result_builder=lambda message: _payload_result(
            {"ok": False, "error": "restore_failed", "message": message},
            status_code=500,
        ),
    )
    if worker_result is not None:
        return worker_result

    return _accepted_operation_result(
        op_id,
        existing=resumed,
        resumed=resumed,
        message="Restore accepted.",
    )


def restore_status(ctx, *, since, job_id=None):
    state = ctx.state
    payload = state["get_restore_status"](since_seq=since, job_id=job_id)
    return _payload_result(payload)


def operation_status(ctx, *, op_id, client_key):
    state = ctx.state
    limited = enforce_rate_limit(ctx, "operation-status", client_key=client_key, limit=90, window_seconds=15.0)
    if limited is not None:
        return limited
    item = state_store_service.get_operation(state["APP_STATE_DB_PATH"], op_id)
    if item is None:
        return _payload_result({"ok": False, "error": "not_found", "message": "Operation not found."}, status_code=404)
    return _payload_result({"ok": True, "operation": item})


def rcon_command(ctx, *, client_key, command, sudo_password):
    state = ctx.state
    limited = enforce_rate_limit(ctx, "rcon", client_key=client_key, limit=20, window_seconds=30.0)
    if limited is not None:
        return limited
    if not command:
        state["log_mcweb_action"]("submit", rejection_message="Command is required.")
        return _response_result(state["_rcon_rejected_response"]("Command is required.", 400))
    if not state["is_rcon_enabled"]():
        state["log_mcweb_action"](
            "submit",
            command=command,
            rejection_message="RCON is disabled: rcon.password not found in server.properties.",
        )
        return _response_result(
            state["_rcon_rejected_response"](
                "RCON is disabled: rcon.password not found in server.properties.",
                503,
            )
        )
    if state["get_status"]() != "active":
        state["log_mcweb_action"]("submit", command=command, rejection_message="Server is not running.")
        return _response_result(state["_rcon_rejected_response"]("Server is not running.", 409))
    if not state["validate_sudo_password"](sudo_password):
        state["log_mcweb_action"]("submit", command=command, rejection_message="Password incorrect.")
        return _response_result(state["_password_rejected_response"]())
    state["record_successful_password_ip"]()

    try:
        result = state["_run_mcrcon"](command, timeout=8)
    except Exception as exc:
        state["log_mcweb_exception"]("rcon_execute", exc)
        state["log_mcweb_action"]("submit", command=command, rejection_message="RCON command failed to execute.")
        return _response_result(state["_rcon_rejected_response"]("RCON command failed to execute.", 500))

    if result.returncode != 0:
        detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
        message = "RCON command failed."
        if detail:
            message = f"RCON command failed: {detail[:400]}"
        state["log_mcweb_action"]("submit", command=command, rejection_message=message)
        return _response_result(state["_rcon_rejected_response"](message, 500))

    state["log_mcweb_action"]("submit", command=command)
    return _response_result(state["_ok_response"]())
