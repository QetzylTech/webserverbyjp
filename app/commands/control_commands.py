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

    # Route: /start
    @app.route("/start", methods=["POST"])
    def start():
        """Runtime helper start."""
        limited = _enforce_rate_limit("start", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        if state["is_storage_low"]():
            message = state["low_storage_error_message"]()
            state["log_mcweb_action"]("start", rejection_message=message)
            return state["_low_storage_blocked_response"](message)
        idempotency_key = _idempotency_key_from_request()
        op_id = ""
        existing = None
        resumed = False
        if not idempotency_key:
            active = _find_active_operation("start")
            if isinstance(active, dict):
                state["log_mcweb_action"]("start")
                return jsonify({
                    "ok": True,
                    "accepted": True,
                    "existing": True,
                    "resumed": False,
                    "op_id": str(active.get("op_id", "") or ""),
                    "status": str(active.get("status", "") or "intent"),
                }), 202
        if idempotency_key:
            try:
                existing = state_store_service.get_operation_by_idempotency_key(
                    state["APP_STATE_DB_PATH"],
                    op_type="start",
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                state["log_mcweb_exception"]("get_operation_by_idempotency_key/start", exc)
                return state["_start_failed_response"]("Failed to load start operation record.")
        if isinstance(existing, dict):
            op_id = str(existing.get("op_id", "") or "")
            status = str(existing.get("status", "") or "").strip().lower()
            if status in {"intent", "in_progress", "observed"}:
                state["log_mcweb_action"]("start")
                return jsonify({
                    "ok": True,
                    "accepted": True,
                    "existing": True,
                    "resumed": False,
                    "op_id": op_id,
                    "status": str(existing.get("status", "") or "intent"),
                }), 202
            if status == "failed" and op_id:
                resumed = True
                try:
                    state_store_service.update_operation(
                        state["APP_STATE_DB_PATH"],
                        op_id=op_id,
                        status="intent",
                        error_code="",
                        message="Resume requested for start operation.",
                        checkpoint="resume_requested",
                        increment_attempt=True,
                    )
                except Exception as exc:
                    state["log_mcweb_exception"]("update_operation/start_resume", exc)
                    return state["_start_failed_response"]("Failed to resume start operation.")

        if not op_id:
            op_id = _new_operation_id("start")
            try:
                state_store_service.create_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    op_type="start",
                    target=state.get("SERVICE", "minecraft"),
                    idempotency_key=idempotency_key,
                    status="intent",
                    checkpoint="intent_created",
                    payload={},
                )
            except Exception as exc:
                state["log_mcweb_exception"]("create_operation/start", exc)
                return state["_start_failed_response"]("Failed to create start operation record.")
        _enqueue_control_intent("start", op_id, target=state.get("SERVICE", "minecraft"))
        _invalidate_observed_cache()
        state["set_service_status_intent"]("starting")
        state["invalidate_status_cache"]()
        _publish_metrics_now()

        # Execute local worker path even in web role so single-process runs
        # do not depend on an external worker process for control actions.

        def _start_worker():
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="in_progress",
                    checkpoint="worker_started",
                    started=True,
                    message="Start operation in progress.",
                )
            except Exception as exc:
                state["log_mcweb_exception"]("update_operation/start_in_progress", exc)
            result = state["start_service_non_blocking"](timeout=12)
            if not result.get("ok"):
                message = result.get("message", "Failed to start service.")
                state["set_service_status_intent"](None)
                state["invalidate_status_cache"]()
                _publish_metrics_now()
                state["log_mcweb_action"]("start-worker", rejection_message=message)
                try:
                    state_store_service.update_operation(
                        state["APP_STATE_DB_PATH"],
                        op_id=op_id,
                        status="failed",
                        error_code="start_failed",
                        checkpoint="start_failed",
                        message=message,
                        finished=True,
                    )
                except Exception as exc:
                    state["log_mcweb_exception"]("update_operation/start_failed", exc)
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
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="in_progress",
                    checkpoint="start_dispatched",
                    message="Start dispatched; awaiting observed active state.",
                    finished=False,
                )
            except Exception as exc:
                    state["log_mcweb_exception"]("update_operation/start_observed", exc)
            _invalidate_observed_cache()
            _publish_metrics_now()

        try:
            start_worker(
                state,
                WorkerSpec(
                    name=f"command-start-{op_id}",
                    target=_start_worker,
                    stop_signal_name=f"command_start_stop_event_{op_id}",
                    health_marker="command_start",
                ),
                threading_module=threading_module,
            )
        except Exception as exc:
            state["set_service_status_intent"](None)
            state["invalidate_status_cache"]()
            _publish_metrics_now()
            state["log_mcweb_exception"]("start-thread", exc)
            state["log_mcweb_action"]("start-worker", rejection_message="Failed to start service worker thread.")
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="failed",
                    error_code="thread_start_failed",
                    checkpoint="thread_start_failed",
                    message="Failed to start service worker thread.",
                    finished=True,
                )
            except Exception:
                pass
            return state["_start_failed_response"]("Failed to start service worker thread.")

        state["log_mcweb_action"]("start")
        return jsonify({
            "ok": True,
            "accepted": True,
            "existing": resumed,
            "resumed": resumed,
            "op_id": op_id,
            "status": "intent",
        }), 202

    # Route: /stop
    @app.route("/stop", methods=["POST"])
    def stop():
        """Runtime helper stop."""
        limited = _enforce_rate_limit("stop", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        sudo_password = request.form.get("sudo_password", "")
        idempotency_key = _idempotency_key_from_request()
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("stop", rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()

        op_id = ""
        existing = None
        resumed = False
        if not idempotency_key:
            active = _find_active_operation("stop")
            if isinstance(active, dict):
                state["log_mcweb_action"]("stop")
                return jsonify({
                    "ok": True,
                    "accepted": True,
                    "existing": True,
                    "resumed": False,
                    "op_id": str(active.get("op_id", "") or ""),
                    "status": str(active.get("status", "") or "intent"),
                }), 202
        if idempotency_key:
            try:
                existing = state_store_service.get_operation_by_idempotency_key(
                    state["APP_STATE_DB_PATH"],
                    op_type="stop",
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                state["log_mcweb_exception"]("get_operation_by_idempotency_key/stop", exc)
                return state["_start_failed_response"]("Failed to load stop operation record.")
        if isinstance(existing, dict):
            op_id = str(existing.get("op_id", "") or "")
            status = str(existing.get("status", "") or "").strip().lower()
            if status in {"intent", "in_progress", "observed"}:
                state["log_mcweb_action"]("stop")
                return jsonify({
                    "ok": True,
                    "accepted": True,
                    "existing": True,
                    "resumed": False,
                    "op_id": op_id,
                    "status": str(existing.get("status", "") or "intent"),
                }), 202
            if status == "failed" and op_id:
                resumed = True
                try:
                    state_store_service.update_operation(
                        state["APP_STATE_DB_PATH"],
                        op_id=op_id,
                        status="intent",
                        error_code="",
                        message="Resume requested for stop operation.",
                        checkpoint="resume_requested",
                        increment_attempt=True,
                    )
                except Exception as exc:
                    state["log_mcweb_exception"]("update_operation/stop_resume", exc)
                    return state["_start_failed_response"]("Failed to resume stop operation.")

        if not op_id:
            op_id = _new_operation_id("stop")
            try:
                state_store_service.create_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    op_type="stop",
                    target=state.get("SERVICE", "minecraft"),
                    idempotency_key=idempotency_key,
                    status="intent",
                    checkpoint="intent_created",
                    payload={},
                )
            except Exception as exc:
                state["log_mcweb_exception"]("create_operation/stop", exc)
                return state["_start_failed_response"]("Failed to create stop operation record.")
        _enqueue_control_intent("stop", op_id, target=state.get("SERVICE", "minecraft"))
        _invalidate_observed_cache()

        state["set_service_status_intent"]("shutting")
        state["invalidate_status_cache"]()
        _publish_metrics_now()

        if process_role == "web":
            state["log_mcweb_action"]("stop")
            return jsonify({
                "ok": True,
                "accepted": True,
                "queued": True,
                "existing": resumed,
                "resumed": resumed,
                "op_id": op_id,
                "status": "intent",
            }), 202

        def _stop_worker():
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="in_progress",
                    checkpoint="worker_started",
                    started=True,
                    message="Stop operation in progress.",
                )
            except Exception as exc:
                state["log_mcweb_exception"]("update_operation/stop_in_progress", exc)

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
                state["set_service_status_intent"](None)
                state["invalidate_status_cache"]()
                _publish_metrics_now()
                state["log_mcweb_action"]("stop-worker", rejection_message=message)
                try:
                    state_store_service.update_operation(
                        state["APP_STATE_DB_PATH"],
                        op_id=op_id,
                        status="failed",
                        error_code="stop_failed",
                        checkpoint="stop_failed",
                        message=message,
                        finished=True,
                    )
                except Exception as exc:
                    state["log_mcweb_exception"]("update_operation/stop_failed", exc)
                return

            state["clear_session_start_time"]()
            state["reset_backup_schedule_state"]()
            run_cleanup_event_if_enabled(getattr(state, "ctx", state), "server_shutdown")
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="observed",
                    checkpoint="observed",
                    message="Service stop observed.",
                    finished=True,
                )
            except Exception as exc:
                state["log_mcweb_exception"]("update_operation/stop_observed", exc)
            _invalidate_observed_cache()
            _publish_metrics_now()

        try:
            start_worker(
                state,
                WorkerSpec(
                    name=f"command-stop-{op_id}",
                    target=_stop_worker,
                    stop_signal_name=f"command_stop_stop_event_{op_id}",
                    health_marker="command_stop",
                ),
                threading_module=threading_module,
            )
        except Exception as exc:
            state["set_service_status_intent"](None)
            state["invalidate_status_cache"]()
            _publish_metrics_now()
            state["log_mcweb_exception"]("stop-thread", exc)
            state["log_mcweb_action"]("stop-worker", rejection_message="Failed to start service stop worker thread.")
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="failed",
                    error_code="thread_start_failed",
                    checkpoint="thread_start_failed",
                    message="Failed to start service stop worker thread.",
                    finished=True,
                )
            except Exception:
                pass
            return state["_start_failed_response"]("Failed to start service stop worker thread.")

        state["log_mcweb_action"]("stop")
        return jsonify({
            "ok": True,
            "accepted": True,
            "existing": resumed,
            "resumed": resumed,
            "op_id": op_id,
            "status": "intent",
        }), 202

    # Route: /backup
    @app.route("/backup", methods=["POST"])
    def backup():
        """Runtime helper backup."""
        limited = _enforce_rate_limit("backup", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        idempotency_key = _idempotency_key_from_request()
        op_id = ""
        existing = None
        resumed = False
        if not idempotency_key:
            active = _find_active_operation("backup")
            if isinstance(active, dict):
                state["log_mcweb_action"]("backup")
                return jsonify({
                    "ok": True,
                    "accepted": True,
                    "existing": True,
                    "resumed": False,
                    "op_id": str(active.get("op_id", "") or ""),
                    "status": str(active.get("status", "") or "intent"),
                }), 202
        if idempotency_key:
            try:
                existing = state_store_service.get_operation_by_idempotency_key(
                    state["APP_STATE_DB_PATH"],
                    op_type="backup",
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                state["log_mcweb_exception"]("get_operation_by_idempotency_key/backup", exc)
                return state["_backup_failed_response"]("Failed to load backup operation record.")
        if isinstance(existing, dict):
            op_id = str(existing.get("op_id", "") or "")
            status = str(existing.get("status", "") or "").strip().lower()
            if status in {"intent", "in_progress", "observed"}:
                state["log_mcweb_action"]("backup")
                return jsonify({
                    "ok": True,
                    "accepted": True,
                    "existing": True,
                    "resumed": False,
                    "op_id": op_id,
                    "status": str(existing.get("status", "") or "intent"),
                }), 202
            if status == "failed" and op_id:
                resumed = True
                try:
                    state_store_service.update_operation(
                        state["APP_STATE_DB_PATH"],
                        op_id=op_id,
                        status="intent",
                        error_code="",
                        message="Resume requested for backup operation.",
                        checkpoint="resume_requested",
                        increment_attempt=True,
                    )
                except Exception as exc:
                    state["log_mcweb_exception"]("update_operation/backup_resume", exc)
                    return state["_backup_failed_response"]("Failed to resume backup operation.")

        if not op_id:
            op_id = _new_operation_id("backup")
            try:
                state_store_service.create_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    op_type="backup",
                    target="manual",
                    idempotency_key=idempotency_key,
                    status="intent",
                    checkpoint="intent_created",
                    payload={"trigger": "manual"},
                )
            except Exception as exc:
                state["log_mcweb_exception"]("create_operation/backup", exc)
                return state["_backup_failed_response"]("Failed to create backup operation record.")
        _enqueue_control_intent("backup", op_id, target="manual")
        _invalidate_observed_cache()

        # Execute local worker path even in web role so single-process runs
        # do not depend on an external worker process for backup actions.

        def _backup_worker():
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="in_progress",
                    checkpoint="worker_started",
                    started=True,
                    message="Backup operation in progress.",
                )
            except Exception as exc:
                state["log_mcweb_exception"]("update_operation/backup_in_progress", exc)
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
                try:
                    state_store_service.update_operation(
                        state["APP_STATE_DB_PATH"],
                        op_id=op_id,
                        status="failed",
                        error_code="backup_failed",
                        checkpoint="backup_failed",
                        message=message,
                        finished=True,
                    )
                except Exception as exc:
                    state["log_mcweb_exception"]("update_operation/backup_failed", exc)
                return
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="observed",
                    checkpoint="observed",
                    message="Backup operation observed complete.",
                    finished=True,
                )
            except Exception as exc:
                state["log_mcweb_exception"]("update_operation/backup_observed", exc)
            _invalidate_observed_cache()

        try:
            start_worker(
                state,
                WorkerSpec(
                    name=f"command-backup-{op_id}",
                    target=_backup_worker,
                    stop_signal_name=f"command_backup_stop_event_{op_id}",
                    health_marker="command_backup",
                ),
                threading_module=threading_module,
            )
        except Exception as exc:
            state["log_mcweb_exception"]("backup-thread", exc)
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="failed",
                    error_code="thread_start_failed",
                    checkpoint="thread_start_failed",
                    message="Failed to start backup worker thread.",
                    finished=True,
                )
            except Exception:
                pass
            return state["_backup_failed_response"]("Failed to start backup worker thread.")

        state["log_mcweb_action"]("backup")
        return jsonify({
            "ok": True,
            "accepted": True,
            "existing": resumed,
            "resumed": resumed,
            "op_id": op_id,
            "status": "intent",
        }), 202

    # Route: /restore-backup
    @app.route("/restore-backup", methods=["POST"])
    def restore_backup():
        """Runtime helper restore_backup."""
        limited = _enforce_rate_limit("restore-backup", limit=6, window_seconds=30.0)
        if limited is not None:
            return limited
        sudo_password = request.form.get("sudo_password", "")
        filename = (request.form.get("filename", "") or "").strip()
        idempotency_key = _idempotency_key_from_request()

        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("restore-backup", command=filename, rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()
        if not filename:
            return jsonify({"ok": False, "error": "restore_failed", "message": "Backup filename is required."}), 400

        op_id = ""
        existing = None
        resumed = False
        if not idempotency_key:
            active_same_target = _find_active_operation("restore", target=filename)
            if isinstance(active_same_target, dict):
                return jsonify({
                    "ok": True,
                    "accepted": True,
                    "message": "Restore accepted.",
                    "existing": True,
                    "resumed": False,
                    "op_id": str(active_same_target.get("op_id", "") or ""),
                    "status": str(active_same_target.get("status", "") or "intent"),
                }), 202
            any_restore = _find_active_operation("restore")
            if isinstance(any_restore, dict):
                return jsonify({
                    "ok": False,
                    "error": "restore_in_progress",
                    "message": "Another restore operation is already in progress.",
                }), 409
        if idempotency_key:
            try:
                existing = state_store_service.get_operation_by_idempotency_key(
                    state["APP_STATE_DB_PATH"],
                    op_type="restore",
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                state["log_mcweb_exception"]("get_operation_by_idempotency_key/restore", exc)
                return jsonify({"ok": False, "error": "restore_failed", "message": "Failed to load restore operation record."}), 500
        if isinstance(existing, dict):
            op_id = str(existing.get("op_id", "") or "")
            existing_target = str(existing.get("target", "") or "")
            if existing_target and existing_target != filename:
                return jsonify({
                    "ok": False,
                    "error": "idempotency_key_conflict",
                    "message": "Idempotency key already used for a different restore target.",
                }), 409
            status = str(existing.get("status", "") or "").strip().lower()
            if status in {"intent", "in_progress", "observed"}:
                return jsonify({
                    "ok": True,
                    "accepted": True,
                    "message": "Restore accepted.",
                    "existing": True,
                    "resumed": False,
                    "op_id": op_id,
                    "status": str(existing.get("status", "") or "intent"),
                }), 202
            if status == "failed" and op_id:
                resumed = True
                try:
                    state_store_service.update_operation(
                        state["APP_STATE_DB_PATH"],
                        op_id=op_id,
                        status="intent",
                        error_code="",
                        message="Resume requested for restore operation.",
                        checkpoint="resume_requested",
                        increment_attempt=True,
                    )
                except Exception as exc:
                    state["log_mcweb_exception"]("update_operation/restore_resume", exc)
                    return jsonify({"ok": False, "error": "restore_failed", "message": "Failed to resume restore operation."}), 500

        if not op_id:
            op_id = _new_operation_id("restore")
            try:
                state_store_service.create_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    op_type="restore",
                    target=filename,
                    idempotency_key=idempotency_key,
                    status="intent",
                    checkpoint="intent_created",
                    payload={},
                )
            except Exception as exc:
                state["log_mcweb_exception"]("create_operation/restore", exc)
                return jsonify({"ok": False, "error": "restore_failed", "message": "Failed to create restore operation record."}), 500
        _enqueue_control_intent("restore", op_id, target=filename)
        _invalidate_observed_cache()

        if process_role == "web":
            return jsonify({
                "ok": True,
                "accepted": True,
                "queued": True,
                "message": "Restore accepted.",
                "existing": resumed,
                "resumed": resumed,
                "op_id": op_id,
                "status": "intent",
            }), 202

        def _restore_worker():
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="in_progress",
                    checkpoint="worker_started",
                    started=True,
                    message="Restore operation in progress.",
                )
            except Exception as exc:
                state["log_mcweb_exception"]("update_operation/restore_in_progress", exc)

            result = state["start_restore_job"](filename)
            if not result.get("ok"):
                message = result.get("message", "Restore failed to start.")
                state["log_mcweb_action"]("restore-backup", command=filename, rejection_message=message)
                try:
                    state_store_service.update_operation(
                        state["APP_STATE_DB_PATH"],
                        op_id=op_id,
                        status="failed",
                        error_code=str(result.get("error", "") or "restore_start_failed"),
                        checkpoint="restore_start_failed",
                        message=message,
                        finished=True,
                    )
                except Exception as exc:
                    state["log_mcweb_exception"]("update_operation/restore_failed", exc)
                return

            restore_job_id = str(result.get("job_id", "") or "")
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="in_progress",
                    checkpoint="restore_job_started",
                    message="Restore worker started.",
                    payload={"restore_job_id": restore_job_id},
                )
            except Exception as exc:
                state["log_mcweb_exception"]("update_operation/restore_job_started", exc)

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
                try:
                    state_store_service.update_operation(
                        state["APP_STATE_DB_PATH"],
                        op_id=op_id,
                        status="observed",
                        checkpoint="observed",
                        message=str(result_payload.get("message", "Restore completed successfully.") or "Restore completed successfully."),
                        payload={"restore_job_id": restore_job_id, "result": result_payload},
                        finished=True,
                    )
                except Exception as exc:
                    state["log_mcweb_exception"]("update_operation/restore_observed", exc)
                state["log_mcweb_action"]("restore-backup", command=f"{filename} (started)")
                _invalidate_observed_cache()
                return

            message = "Restore failed."
            error_code = "restore_failed"
            if isinstance(result_payload, dict):
                message = str(result_payload.get("message", message) or message)
                error_code = str(result_payload.get("error", error_code) or error_code)
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="failed",
                    error_code=error_code,
                    checkpoint="restore_failed",
                    message=message,
                    payload={"restore_job_id": restore_job_id, "result": result_payload if isinstance(result_payload, dict) else {}},
                    finished=True,
                )
            except Exception as exc:
                state["log_mcweb_exception"]("update_operation/restore_terminal_failed", exc)
            _invalidate_observed_cache()

        try:
            start_worker(
                state,
                WorkerSpec(
                    name=f"command-restore-{op_id}",
                    target=_restore_worker,
                    stop_signal_name=f"command_restore_stop_event_{op_id}",
                    health_marker="command_restore",
                ),
                threading_module=threading_module,
            )
        except Exception as exc:
            state["log_mcweb_exception"]("restore-thread", exc)
            try:
                state_store_service.update_operation(
                    state["APP_STATE_DB_PATH"],
                    op_id=op_id,
                    status="failed",
                    error_code="thread_start_failed",
                    checkpoint="thread_start_failed",
                    message="Failed to start restore worker thread.",
                    finished=True,
                )
            except Exception:
                pass
            return jsonify({"ok": False, "error": "restore_failed", "message": "Failed to start restore worker thread."}), 500

        return jsonify({
            "ok": True,
            "accepted": True,
            "message": "Restore accepted.",
            "existing": resumed,
            "resumed": resumed,
            "op_id": op_id,
            "status": "intent",
        }), 202

    # Route: /restore-status
    @app.route("/restore-status")
    def restore_status():
        """Runtime helper restore_status."""
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
        """Runtime helper rcon."""
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

