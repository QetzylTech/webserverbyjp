"""Command handlers for control-plane start/stop/backup/restore actions."""
import threading
import time
import uuid

from flask import jsonify, request
from app.core import state_store as state_store_service
from app.core.rate_limit import InMemoryRateLimiter
from app.services.worker_scheduler import WorkerSpec, start_worker


_CONTROL_RATE_LIMITER = InMemoryRateLimiter()


def register_control_routes(app, state, *, run_cleanup_event_if_enabled, threading_module=threading):
    """Register start/stop/backup/restore/RCON control routes."""
    process_role = str(state.get("PROCESS_ROLE", "all") or "all").strip().lower()

    def _new_operation_id(prefix):
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def _invalidate_observed_cache():
        invalidate_fn = state.get("invalidate_observed_state_cache")
        if callable(invalidate_fn):
            try:
                invalidate_fn()
            except Exception:
                pass

    def _publish_metrics_now():
        publish_fn = state.get("_collect_and_publish_metrics") or state.get("collect_and_publish_metrics")
        if callable(publish_fn):
            try:
                publish_fn()
            except Exception:
                pass

    def _refresh_runtime_status(intent=None, *, invalidate_observed=False):
        if invalidate_observed:
            _invalidate_observed_cache()
        if intent is None:
            state["set_service_status_intent"](None)
        else:
            state["set_service_status_intent"](intent)
        state["invalidate_status_cache"]()
        _publish_metrics_now()

    def _idempotency_key_from_request():
        header_value = (request.headers.get("X-Idempotency-Key", "") or "").strip()
        form_value = (request.form.get("idempotency_key", "") or "").strip()
        return header_value or form_value

    def _enqueue_control_intent(op_type, op_id, *, target=""):
        try:
            state_store_service.append_event(
                state["APP_STATE_DB_PATH"],
                topic="control_intent",
                payload={
                    "op_type": str(op_type or ""),
                    "op_id": str(op_id or ""),
                    "target": str(target or ""),
                },
            )
            return True
        except Exception as exc:
            state["log_mcweb_exception"](f"append_event/control_intent/{op_type}", exc)
            return False

    def _client_key():
        getter = state.get("_get_client_ip")
        if callable(getter):
            try:
                return str(getter() or "unknown")
            except Exception:
                pass
        xff = (request.headers.get("X-Forwarded-For", "") or "").strip()
        if xff:
            return xff.split(",")[0].strip()
        return str(request.remote_addr or "unknown")

    def _enforce_rate_limit(route_key, *, limit, window_seconds):
        allowed, retry_after = _CONTROL_RATE_LIMITER.allow(
            f"{route_key}:{_client_key()}",
            limit=limit,
            window_seconds=window_seconds,
        )
        if allowed:
            return None
        response = jsonify({
            "ok": False,
            "error": "rate_limited",
            "message": "Too many requests for this action. Please retry shortly.",
            "retry_after_seconds": retry_after,
        })
        response.status_code = 429
        response.headers["Retry-After"] = str(int(retry_after))
        return response

    def _find_active_operation(op_type, *, target=None):
        try:
            rows = state_store_service.list_operations_by_status(
                state["APP_STATE_DB_PATH"],
                statuses=("intent", "in_progress"),
                limit=40,
            )
        except Exception:
            return None
        target_text = str(target or "")
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("op_type", "") or "").strip().lower() != str(op_type or "").strip().lower():
                continue
            if target is not None and str(row.get("target", "") or "") != target_text:
                continue
            return row
        return None

    def _accepted_operation_response(op_id, *, status="intent", existing=False, resumed=False, queued=False, message=None):
        payload = {
            "ok": True,
            "accepted": True,
            "existing": bool(existing),
            "resumed": bool(resumed),
            "op_id": str(op_id or ""),
            "status": str(status or "intent"),
        }
        if queued:
            payload["queued"] = True
        if message is not None:
            payload["message"] = str(message)
        return jsonify(payload), 202

    def _update_operation_record(op_id, log_key, **fields):
        try:
            state_store_service.update_operation(
                state["APP_STATE_DB_PATH"],
                op_id=op_id,
                **fields,
            )
            return True
        except Exception as exc:
            state["log_mcweb_exception"](f"update_operation/{log_key}", exc)
            return False

    def _load_existing_operation(op_type, idempotency_key, *, error_log_key, error_response):
        if not idempotency_key:
            return None, None
        try:
            existing = state_store_service.get_operation_by_idempotency_key(
                state["APP_STATE_DB_PATH"],
                op_type=op_type,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            state["log_mcweb_exception"](error_log_key, exc)
            return None, error_response
        return existing, None

    def _resume_operation(op_id, *, op_type, error_response):
        resumed = _update_operation_record(
            op_id,
            f"{op_type}_resume",
            status="intent",
            error_code="",
            message=f"Resume requested for {op_type} operation.",
            checkpoint="resume_requested",
            increment_attempt=True,
        )
        if resumed:
            return True, None
        return False, error_response

    def _create_operation(op_type, op_id, *, target, idempotency_key, payload, error_log_key, error_response):
        try:
            state_store_service.create_operation(
                state["APP_STATE_DB_PATH"],
                op_id=op_id,
                op_type=op_type,
                target=target,
                idempotency_key=idempotency_key,
                status="intent",
                checkpoint="intent_created",
                payload=payload,
            )
        except Exception as exc:
            state["log_mcweb_exception"](error_log_key, exc)
            return error_response
        return None

    def _reuse_or_resume_existing_operation(
        existing,
        *,
        op_type,
        resume_error_response,
        accepted_message=None,
        accepted_statuses=("intent", "in_progress", "observed"),
        expected_target=None,
        target_conflict_response=None,
        log_action=None,
    ):
        if not isinstance(existing, dict):
            return "", False, None
        op_id = str(existing.get("op_id", "") or "")
        existing_target = str(existing.get("target", "") or "")
        if expected_target is not None and existing_target and existing_target != str(expected_target):
            if callable(target_conflict_response):
                return op_id, False, target_conflict_response()
        status = str(existing.get("status", "") or "").strip().lower()
        if status in set(accepted_statuses):
            if log_action:
                state["log_mcweb_action"](log_action)
            return op_id, False, _accepted_operation_response(
                op_id,
                status=str(existing.get("status", "") or "intent"),
                existing=True,
                resumed=False,
                message=accepted_message,
            )
        if status == "failed" and op_id:
            resumed, error_response = _resume_operation(op_id, op_type=op_type, error_response=resume_error_response)
            if error_response is not None:
                return op_id, False, error_response
            return op_id, resumed, None
        return op_id, False, None

    def _start_operation_worker(
        op_type,
        op_id,
        *,
        target,
        thread_error_message,
        error_response,
        on_thread_start_failed=None,
    ):
        try:
            start_worker(
                state,
                WorkerSpec(
                    name=f"command-{op_type}-{op_id}",
                    target=target,
                    stop_signal_name=f"command_{op_type}_stop_event_{op_id}",
                    health_marker=f"command_{op_type}",
                ),
                threading_module=threading_module,
            )
        except Exception as exc:
            if callable(on_thread_start_failed):
                on_thread_start_failed()
            state["log_mcweb_exception"](f"{op_type}-thread", exc)
            _update_operation_record(
                op_id,
                f"{op_type}_thread_start_failed",
                status="failed",
                error_code="thread_start_failed",
                checkpoint="thread_start_failed",
                message=thread_error_message,
                finished=True,
            )
            return error_response(thread_error_message)
        return None

    def _active_operation_response(op_type, *, target=None, log_action=None, message=None):
        active = _find_active_operation(op_type, target=target)
        if not isinstance(active, dict):
            return None
        if log_action:
            state["log_mcweb_action"](log_action)
        return _accepted_operation_response(
            active.get("op_id", ""),
            status=str(active.get("status", "") or "intent"),
            existing=True,
            message=message,
        )

    def _prepare_operation(
        op_type,
        *,
        target,
        payload,
        active_target=None,
        active_message=None,
        active_log_action=None,
        active_conflict_response=None,
        accepted_message=None,
        log_action=None,
        load_error_response,
        resume_error_response,
        create_error_response,
        target_conflict_response=None,
    ):
        idempotency_key = _idempotency_key_from_request()
        if not idempotency_key:
            active_response = _active_operation_response(
                op_type,
                target=active_target,
                log_action=active_log_action,
                message=active_message,
            )
            if active_response is not None:
                return "", False, active_response
            if callable(active_conflict_response):
                any_active = _find_active_operation(op_type)
                if isinstance(any_active, dict):
                    return "", False, active_conflict_response()

        existing, error_response = _load_existing_operation(
            op_type,
            idempotency_key,
            error_log_key=f"get_operation_by_idempotency_key/{op_type}",
            error_response=load_error_response,
        )
        if error_response is not None:
            return "", False, error_response

        op_id, resumed, reuse_response = _reuse_or_resume_existing_operation(
            existing,
            op_type=op_type,
            resume_error_response=resume_error_response,
            accepted_message=accepted_message,
            expected_target=target,
            target_conflict_response=target_conflict_response,
            log_action=log_action,
        )
        if reuse_response is not None:
            return op_id, resumed, reuse_response

        if not op_id:
            op_id = _new_operation_id(op_type)
            error_response = _create_operation(
                op_type,
                op_id,
                target=target,
                idempotency_key=idempotency_key,
                payload=payload,
                error_log_key=f"create_operation/{op_type}",
                error_response=create_error_response,
            )
            if error_response is not None:
                return "", False, error_response

        return op_id, resumed, None

    # Route: /start
    @app.route("/start", methods=["POST"])
    def start():
        """Queue a start operation and begin local execution when possible."""
        limited = _enforce_rate_limit("start", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        if state["is_storage_low"]():
            message = state["low_storage_error_message"]()
            state["log_mcweb_action"]("start", rejection_message=message)
            return state["_low_storage_blocked_response"](message)

        op_id, resumed, response = _prepare_operation(
            "start",
            target=state.get("SERVICE", "minecraft"),
            payload={},
            active_log_action="start",
            log_action="start",
            load_error_response=state["_start_failed_response"]("Failed to load start operation record."),
            resume_error_response=state["_start_failed_response"]("Failed to resume start operation."),
            create_error_response=state["_start_failed_response"]("Failed to create start operation record."),
        )
        if response is not None:
            return response

        _enqueue_control_intent("start", op_id, target=state.get("SERVICE", "minecraft"))
        _refresh_runtime_status("starting", invalidate_observed=True)

        def _start_worker():
            _update_operation_record(
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
                _refresh_runtime_status(None)
                state["log_mcweb_action"]("start-worker", rejection_message=message)
                _update_operation_record(
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
                op_id,
                "start_observed",
                status="in_progress",
                checkpoint="start_dispatched",
                message="Start dispatched; awaiting observed active state.",
                finished=False,
            )
            _refresh_runtime_status("starting", invalidate_observed=True)

        worker_response = _start_operation_worker(
            "start",
            op_id,
            target=_start_worker,
            thread_error_message="Failed to start service worker thread.",
            error_response=state["_start_failed_response"],
            on_thread_start_failed=lambda: (
                _refresh_runtime_status(None),
                state["log_mcweb_action"]("start-worker", rejection_message="Failed to start service worker thread."),
            ),
        )
        if worker_response is not None:
            return worker_response

        state["log_mcweb_action"]("start")
        return _accepted_operation_response(op_id, existing=resumed, resumed=resumed)

    # Route: /stop
    @app.route("/stop", methods=["POST"])
    def stop():
        """Queue a stop operation and run it locally outside web-only mode."""
        limited = _enforce_rate_limit("stop", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        sudo_password = request.form.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("stop", rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()

        op_id, resumed, response = _prepare_operation(
            "stop",
            target=state.get("SERVICE", "minecraft"),
            payload={},
            active_log_action="stop",
            log_action="stop",
            load_error_response=state["_start_failed_response"]("Failed to load stop operation record."),
            resume_error_response=state["_start_failed_response"]("Failed to resume stop operation."),
            create_error_response=state["_start_failed_response"]("Failed to create stop operation record."),
        )
        if response is not None:
            return response

        _enqueue_control_intent("stop", op_id, target=state.get("SERVICE", "minecraft"))
        _refresh_runtime_status("shutting", invalidate_observed=True)

        if process_role == "web":
            state["log_mcweb_action"]("stop")
            return _accepted_operation_response(op_id, existing=resumed, resumed=resumed, queued=True)

        def _stop_worker():
            _update_operation_record(
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
                _refresh_runtime_status(None)
                state["log_mcweb_action"]("stop-worker", rejection_message=message)
                _update_operation_record(
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
            run_cleanup_event_if_enabled(getattr(state, "ctx", state), "server_shutdown")
            _update_operation_record(
                op_id,
                "stop_observed",
                status="observed",
                checkpoint="observed",
                message="Service stop observed.",
                finished=True,
            )
            _refresh_runtime_status(None, invalidate_observed=True)

        worker_response = _start_operation_worker(
            "stop",
            op_id,
            target=_stop_worker,
            thread_error_message="Failed to start service stop worker thread.",
            error_response=state["_start_failed_response"],
            on_thread_start_failed=lambda: (
                _refresh_runtime_status(None),
                state["log_mcweb_action"]("stop-worker", rejection_message="Failed to start service stop worker thread."),
            ),
        )
        if worker_response is not None:
            return worker_response

        state["log_mcweb_action"]("stop")
        return _accepted_operation_response(op_id, existing=resumed, resumed=resumed)

    # Route: /backup
    @app.route("/backup", methods=["POST"])
    def backup():
        """Queue a manual backup and run it in a worker thread."""
        limited = _enforce_rate_limit("backup", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited

        op_id, resumed, response = _prepare_operation(
            "backup",
            target="manual",
            payload={"trigger": "manual"},
            active_log_action="backup",
            log_action="backup",
            load_error_response=state["_backup_failed_response"]("Failed to load backup operation record."),
            resume_error_response=state["_backup_failed_response"]("Failed to resume backup operation."),
            create_error_response=state["_backup_failed_response"]("Failed to create backup operation record."),
        )
        if response is not None:
            return response

        _enqueue_control_intent("backup", op_id, target="manual")
        _invalidate_observed_cache()

        def _backup_worker():
            _update_operation_record(
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
                op_id,
                "backup_observed",
                status="observed",
                checkpoint="observed",
                message="Backup operation observed complete.",
                finished=True,
            )
            _invalidate_observed_cache()

        worker_response = _start_operation_worker(
            "backup",
            op_id,
            target=_backup_worker,
            thread_error_message="Failed to start backup worker thread.",
            error_response=state["_backup_failed_response"],
        )
        if worker_response is not None:
            return worker_response

        state["log_mcweb_action"]("backup")
        return _accepted_operation_response(op_id, existing=resumed, resumed=resumed)

    # Route: /restore-backup
    @app.route("/restore-backup", methods=["POST"])
    def restore_backup():
        """Queue a restore operation for one backup archive and monitor its job."""
        limited = _enforce_rate_limit("restore-backup", limit=6, window_seconds=30.0)
        if limited is not None:
            return limited
        sudo_password = request.form.get("sudo_password", "")
        filename = (request.form.get("filename", "") or "").strip()
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("restore-backup", command=filename, rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()
        if not filename:
            return jsonify({"ok": False, "error": "restore_failed", "message": "Backup filename is required."}), 400

        op_id, resumed, response = _prepare_operation(
            "restore",
            target=filename,
            payload={},
            active_target=filename,
            active_message="Restore accepted.",
            active_conflict_response=lambda: (jsonify({
                "ok": False,
                "error": "restore_in_progress",
                "message": "Another restore operation is already in progress.",
            }), 409),
            accepted_message="Restore accepted.",
            load_error_response=(jsonify({"ok": False, "error": "restore_failed", "message": "Failed to load restore operation record."}), 500),
            resume_error_response=(jsonify({"ok": False, "error": "restore_failed", "message": "Failed to resume restore operation."}), 500),
            create_error_response=(jsonify({"ok": False, "error": "restore_failed", "message": "Failed to create restore operation record."}), 500),
            target_conflict_response=lambda: (jsonify({
                "ok": False,
                "error": "idempotency_key_conflict",
                "message": "Idempotency key already used for a different restore target.",
            }), 409),
        )
        if response is not None:
            return response

        _enqueue_control_intent("restore", op_id, target=filename)
        _invalidate_observed_cache()

        if process_role == "web":
            return _accepted_operation_response(
                op_id,
                existing=resumed,
                resumed=resumed,
                queued=True,
                message="Restore accepted.",
            )

        def _restore_worker():
            _update_operation_record(
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
                    op_id,
                    "restore_observed",
                    status="observed",
                    checkpoint="observed",
                    message=str(result_payload.get("message", "Restore completed successfully.") or "Restore completed successfully."),
                    payload={"restore_job_id": restore_job_id, "result": result_payload},
                    finished=True,
                )
                state["log_mcweb_action"]("restore-backup", command=f"{filename} (started)")
                _invalidate_observed_cache()
                return

            message = "Restore failed."
            error_code = "restore_failed"
            if isinstance(result_payload, dict):
                message = str(result_payload.get("message", message) or message)
                error_code = str(result_payload.get("error", error_code) or error_code)
            _update_operation_record(
                op_id,
                "restore_terminal_failed",
                status="failed",
                error_code=error_code,
                checkpoint="restore_failed",
                message=message,
                payload={"restore_job_id": restore_job_id, "result": result_payload if isinstance(result_payload, dict) else {}},
                finished=True,
            )
            _invalidate_observed_cache()

        worker_response = _start_operation_worker(
            "restore",
            op_id,
            target=_restore_worker,
            thread_error_message="Failed to start restore worker thread.",
            error_response=lambda message: (jsonify({"ok": False, "error": "restore_failed", "message": message}), 500),
        )
        if worker_response is not None:
            return worker_response

        return _accepted_operation_response(
            op_id,
            existing=resumed,
            resumed=resumed,
            message="Restore accepted.",
        )

    # Route: /restore-status
    @app.route("/restore-status")
    def restore_status():
        """Return streamed restore-job progress for the requested job id."""
        since = request.args.get("since", "0")
        job_id = (request.args.get("job_id", "") or "").strip() or None
        payload = state["get_restore_status"](since_seq=since, job_id=job_id)
        return jsonify(payload)

    # Route: /operation-status/<op_id>
    @app.route("/operation-status/<op_id>")
    def operation_status(op_id):
        """Return one async control-plane operation status by operation id."""
        limited = _enforce_rate_limit("operation-status", limit=90, window_seconds=15.0)
        if limited is not None:
            return limited
        item = state_store_service.get_operation(state["APP_STATE_DB_PATH"], op_id)
        if item is None:
            return jsonify({"ok": False, "error": "not_found", "message": "Operation not found."}), 404
        return jsonify({"ok": True, "operation": item})

    # Route: /rcon
    @app.route("/rcon", methods=["POST"])
    def rcon():
        """Run one RCON command after validation and rate limiting."""
        limited = _enforce_rate_limit("rcon", limit=20, window_seconds=30.0)
        if limited is not None:
            return limited
        command = request.form.get("rcon_command", "").strip()
        sudo_password = request.form.get("sudo_password", "")
        if not command:
            state["log_mcweb_action"]("submit", rejection_message="Command is required.")
            return state["_rcon_rejected_response"]("Command is required.", 400)
        if not state["is_rcon_enabled"]():
            state["log_mcweb_action"](
                "submit",
                command=command,
                rejection_message="RCON is disabled: rcon.password not found in server.properties.",
            )
            return state["_rcon_rejected_response"](
                "RCON is disabled: rcon.password not found in server.properties.",
                503,
            )
        if state["get_status"]() != "active":
            state["log_mcweb_action"]("submit", command=command, rejection_message="Server is not running.")
            return state["_rcon_rejected_response"]("Server is not running.", 409)
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("submit", command=command, rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()

        try:
            result = state["_run_mcrcon"](command, timeout=8)
        except Exception as exc:
            state["log_mcweb_exception"]("rcon_execute", exc)
            state["log_mcweb_action"]("submit", command=command, rejection_message="RCON command failed to execute.")
            return state["_rcon_rejected_response"]("RCON command failed to execute.", 500)

        if result.returncode != 0:
            detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
            message = "RCON command failed."
            if detail:
                message = f"RCON command failed: {detail[:400]}"
            state["log_mcweb_action"]("submit", command=command, rejection_message=message)
            return state["_rcon_rejected_response"](message, 500)

        state["log_mcweb_action"]("submit", command=command)
        return state["_ok_response"]()

