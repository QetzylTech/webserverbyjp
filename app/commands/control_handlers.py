"""Command handlers for control-plane start/stop/backup/restore actions."""
from __future__ import annotations

import json
import time
from datetime import datetime

from flask import Response, stream_with_context

from app.core import state_store as state_store_service
from app.services import maintenance_engine as maintenance_engine_service
from app.services.operation_state import has_pending_operation
from app.services.restore_status import restore_running_from_getter, append_restore_event
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
from app.commands.control_types import CommandResult

_STARTING_STATES = {"activating", "starting"}
_SHUTTING_STATES = {"deactivating", "shutting_down"}


def _service_state_snapshot(state):
    raw = ""
    try:
        raw = str(state["get_status"]() or "").strip().lower()
    except Exception:
        raw = ""
    try:
        intent = str(state.get("get_service_status_intent", lambda: "")() or "").strip().lower()
    except Exception:
        intent = ""
    off_states = {str(item or "").strip().lower() for item in state.get("OFF_STATES", {"inactive", "failed"})}
    is_off = raw in off_states and intent not in {"starting", "shutting"}
    is_starting = raw in _STARTING_STATES or intent == "starting"
    is_shutting = raw in _SHUTTING_STATES or intent == "shutting"
    return raw, intent, is_off, is_starting, is_shutting


def _reject_invalid_state(message, *, error="invalid_state", status_code=409):
    return _payload_result({"ok": False, "error": error, "message": message}, status_code=status_code)


def _restore_in_progress(ctx):
    try:
        state = ctx.state
    except Exception:
        state = {}
    return restore_running_from_getter(state.get("get_restore_status"))


def _cleanup_in_progress():
    return bool(maintenance_engine_service.cleanup_lock_held())


def start_operation(ctx, *, idempotency_key, client_key):
    state = ctx.state
    limited = enforce_rate_limit(ctx, "start", client_key=client_key, limit=8, window_seconds=30.0)
    if limited is not None:
        return limited
    _raw, intent, is_off, is_starting, is_shutting = _service_state_snapshot(state)
    if not is_off:
        state["log_mcweb_action"]("start", rejection_message="Server is not off.")
        return _reject_invalid_state("Server is not off.")
    if is_starting or is_shutting or intent == "starting":
        state["log_mcweb_action"]("start", rejection_message="Start already queued or in progress.")
        return _reject_invalid_state("Start already queued or in progress.")
    guard = state.get("storage_guard")
    if guard is not None and not guard.is_storage_sufficient(state, "start"):
        message = guard.block_message(state, "start")
        state["log_mcweb_action"]("start", rejection_message=message)
        return _response_result(state["_low_storage_blocked_response"](message))
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
    _raw, _intent, is_off, _is_starting, _is_shutting = _service_state_snapshot(state)
    if is_off:
        state["log_mcweb_action"]("stop", rejection_message="Server is already off.")
        return _reject_invalid_state("Server is already off.")
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
    _raw, _intent, _is_off, is_starting, is_shutting = _service_state_snapshot(state)
    if is_starting or is_shutting:
        state["log_mcweb_action"]("backup", rejection_message="Backup unavailable while server is starting or shutting down.")
        return _reject_invalid_state("Backup unavailable while server is starting or shutting down.")
    if _cleanup_in_progress():
        state["log_mcweb_action"]("backup", rejection_message="Cleanup is running.")
        return _reject_invalid_state("Cleanup is running.")
    if _restore_in_progress(ctx) or has_pending_operation(state, "restore"):
        state["log_mcweb_action"]("backup", rejection_message="Restore is running or queued.")
        return _reject_invalid_state("Restore is running or queued.")
    guard = state.get("storage_guard")
    if guard is not None and not guard.is_storage_sufficient(state, "backup"):
        message = guard.block_message(state, "backup")
        state["log_mcweb_action"]("backup", rejection_message=message)
        return _response_result(state["_low_storage_blocked_response"](message))
    if state["is_storage_low"]():
        message = state["low_storage_error_message"]()
        state["log_mcweb_action"]("backup", rejection_message=message)
        return _response_result(state["_low_storage_blocked_response"](message))

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

    _raw_status, _intent, is_off, _is_starting, _is_shutting = _service_state_snapshot(state)
    if not is_off:
        state["log_mcweb_action"]("restore-backup", command=filename, rejection_message="Restore is only available when server is Off.")
        return _reject_invalid_state("Restore is only available when server is Off.")
    if _cleanup_in_progress():
        state["log_mcweb_action"]("restore-backup", command=filename, rejection_message="Cleanup is running.")
        return _reject_invalid_state("Cleanup is running.")
    if state["is_backup_running"]() or has_pending_operation(state, "backup"):
        state["log_mcweb_action"]("restore-backup", command=filename, rejection_message="Backup is running or queued.")
        return _reject_invalid_state("Backup is running or queued.")

    try:
        client_ip = state.get("_get_client_ip")() if callable(state.get("_get_client_ip")) else ""
        device_map = state.get("get_device_name_map")() if callable(state.get("get_device_name_map")) else {}
        device_name = (device_map.get(client_ip, "") or "unmapped-device").strip()
        display_tz = state.get("DISPLAY_TZ")
        now = datetime.now(tz=display_tz) if display_tz else datetime.utcnow()
        stamp = now.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
        append_restore_event(
            ctx,
            f"Restore requested for {filename} by {device_name} ({client_ip or 'unknown'}) at {stamp}.",
        )
    except Exception:
        pass

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


def restore_log_stream(ctx, *, since, job_id=None):
    state = ctx.state
    try:
        since_seq = int(since or 0)
    except (TypeError, ValueError):
        since_seq = 0
    requested_job_id = str(job_id or "").strip() or None

    def generate():
        last_seq = since_seq
        status_sent = False
        db_path = state.get("APP_STATE_DB_PATH")
        while True:
            rows = []
            if db_path:
                try:
                    rows = state_store_service.list_events_since(
                        db_path,
                        topic="restore_log",
                        since_id=last_seq,
                        limit=400,
                    )
                except Exception:
                    rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                row_job_id = str(payload.get("job_id", "") or "")
                if requested_job_id and row_job_id and row_job_id != requested_job_id:
                    continue
                seq = int(row.get("id", last_seq) or last_seq)
                last_seq = max(last_seq, seq)
                data = {
                    "type": "line",
                    "seq": seq,
                    "at": payload.get("at") or row.get("created_at"),
                    "message": payload.get("message", ""),
                }
                yield f"id: {seq}\n"
                yield "event: line\n"
                yield f"data: {json.dumps(data)}\n\n"
            payload = state["get_restore_status"](since_seq=0, job_id=requested_job_id)
            if not isinstance(payload, dict):
                payload = {"ok": False}
            if not payload.get("running") and not status_sent:
                status_sent = True
                status_payload = {
                    "type": "status",
                    "payload": payload,
                }
                yield "event: status\n"
                yield f"data: {json.dumps(status_payload)}\n\n"
                return
            yield ": keepalive\n\n"
            time.sleep(1.0)

    response = Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    return CommandResult(response=response)


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


