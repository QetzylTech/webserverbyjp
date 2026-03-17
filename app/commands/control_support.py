"""Shared helpers for control-plane command handlers."""
from __future__ import annotations

import uuid

from app.core import state_store as state_store_service
from app.core.rate_limit import InMemoryRateLimiter
from app.services.worker_scheduler import WorkerSpec, start_worker

from app.commands.control_types import CommandResult


_CONTROL_RATE_LIMITER = InMemoryRateLimiter()


def _response_result(response):
    return CommandResult(response=response)


def _payload_result(payload, *, status_code=200, headers=None):
    return CommandResult(payload=payload, status_code=int(status_code), headers=headers)


def _accepted_operation_payload(op_id, *, status="intent", existing=False, resumed=False, queued=False, message=None):
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
    return payload


def _accepted_operation_result(op_id, *, status="intent", existing=False, resumed=False, queued=False, message=None):
    return _payload_result(
        _accepted_operation_payload(
            op_id,
            status=status,
            existing=existing,
            resumed=resumed,
            queued=queued,
            message=message,
        ),
        status_code=202,
    )


def enforce_rate_limit(ctx, route_key, *, client_key, limit, window_seconds):
    allowed, retry_after = _CONTROL_RATE_LIMITER.allow(
        f"{route_key}:{client_key}",
        limit=limit,
        window_seconds=window_seconds,
    )
    if allowed:
        return None
    payload = {
        "ok": False,
        "error": "rate_limited",
        "message": "Too many requests for this action. Please retry shortly.",
        "retry_after_seconds": retry_after,
    }
    return _payload_result(payload, status_code=429, headers={"Retry-After": str(int(retry_after))})


def _new_operation_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _invalidate_observed_cache(ctx):
    state = ctx.state
    invalidate_fn = state.get("invalidate_observed_state_cache")
    if callable(invalidate_fn):
        try:
            invalidate_fn()
        except Exception:
            pass


def _publish_metrics_now(ctx):
    state = ctx.state
    publish_fn = state.get("_collect_and_publish_metrics") or state.get("collect_and_publish_metrics")
    if callable(publish_fn):
        try:
            publish_fn()
        except Exception:
            pass


def _refresh_runtime_status(ctx, intent=None, *, invalidate_observed=False):
    state = ctx.state
    if invalidate_observed:
        _invalidate_observed_cache(ctx)
    if intent is None:
        state["set_service_status_intent"](None)
    else:
        state["set_service_status_intent"](intent)
    state["invalidate_status_cache"]()
    _publish_metrics_now(ctx)


def _enqueue_control_intent(ctx, op_type, op_id, *, target=""):
    state = ctx.state
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


def _find_active_operation(ctx, op_type, *, target=None):
    state = ctx.state
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


def _update_operation_record(ctx, op_id, log_key, **fields):
    state = ctx.state
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


def _load_existing_operation(ctx, op_type, idempotency_key, *, error_result):
    if not idempotency_key:
        return None, None
    state = ctx.state
    try:
        existing = state_store_service.get_operation_by_idempotency_key(
            state["APP_STATE_DB_PATH"],
            op_type=op_type,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        state["log_mcweb_exception"](f"get_operation_by_idempotency_key/{op_type}", exc)
        return None, error_result
    return existing, None


def _resume_operation(ctx, op_id, *, op_type, error_result):
    resumed = _update_operation_record(
        ctx,
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
    return False, error_result


def _create_operation(ctx, op_type, op_id, *, target, idempotency_key, payload, error_result):
    state = ctx.state
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
        state["log_mcweb_exception"](f"create_operation/{op_type}", exc)
        return error_result
    return None


def _reuse_or_resume_existing_operation(
    ctx,
    existing,
    *,
    op_type,
    resume_error_result,
    accepted_message=None,
    accepted_statuses=("intent", "in_progress", "observed"),
    expected_target=None,
    target_conflict_result=None,
    log_action=None,
):
    if not isinstance(existing, dict):
        return "", False, None
    state = ctx.state
    op_id = str(existing.get("op_id", "") or "")
    existing_target = str(existing.get("target", "") or "")
    if expected_target is not None and existing_target and existing_target != str(expected_target):
        if target_conflict_result is not None:
            return op_id, False, target_conflict_result
    status = str(existing.get("status", "") or "").strip().lower()
    if status in set(accepted_statuses):
        if log_action:
            state["log_mcweb_action"](log_action)
        return op_id, False, _accepted_operation_result(
            op_id,
            status=str(existing.get("status", "") or "intent"),
            existing=True,
            resumed=False,
            message=accepted_message,
        )
    if status == "failed" and op_id:
        resumed, error_result = _resume_operation(ctx, op_id, op_type=op_type, error_result=resume_error_result)
        if error_result is not None:
            return op_id, False, error_result
        return op_id, resumed, None
    return op_id, False, None


def _start_operation_worker(
    ctx,
    op_type,
    op_id,
    *,
    target,
    thread_error_message,
    error_result_builder,
    on_thread_start_failed=None,
):
    state = ctx.state
    try:
        start_worker(
            state,
            WorkerSpec(
                name=f"command-{op_type}-{op_id}",
                target=target,
                stop_signal_name=f"command_{op_type}_stop_event_{op_id}",
                health_marker=f"command_{op_type}",
            ),
            threading_module=ctx.threading_module,
        )
    except Exception as exc:
        if callable(on_thread_start_failed):
            on_thread_start_failed()
        state["log_mcweb_exception"](f"{op_type}-thread", exc)
        _update_operation_record(
            ctx,
            op_id,
            f"{op_type}_thread_start_failed",
            status="failed",
            error_code="thread_start_failed",
            checkpoint="thread_start_failed",
            message=thread_error_message,
            finished=True,
        )
        return error_result_builder(thread_error_message)
    return None


def _active_operation_response(ctx, op_type, *, target=None, log_action=None, message=None):
    active = _find_active_operation(ctx, op_type, target=target)
    if not isinstance(active, dict):
        return None
    state = ctx.state
    if log_action:
        state["log_mcweb_action"](log_action)
    return _accepted_operation_result(
        active.get("op_id", ""),
        status=str(active.get("status", "") or "intent"),
        existing=True,
        message=message,
    )


def _prepare_operation(
    ctx,
    op_type,
    *,
    target,
    payload,
    idempotency_key,
    active_target=None,
    active_message=None,
    active_log_action=None,
    active_conflict_result=None,
    accepted_message=None,
    log_action=None,
    load_error_result,
    resume_error_result,
    create_error_result,
    target_conflict_result=None,
):
    if not idempotency_key:
        active_response = _active_operation_response(
            ctx,
            op_type,
            target=active_target,
            log_action=active_log_action,
            message=active_message,
        )
        if active_response is not None:
            return "", False, active_response
        if active_conflict_result is not None:
            any_active = _find_active_operation(ctx, op_type)
            if isinstance(any_active, dict):
                return "", False, active_conflict_result

    existing, error_result = _load_existing_operation(
        ctx,
        op_type,
        idempotency_key,
        error_result=load_error_result,
    )
    if error_result is not None:
        return "", False, error_result

    op_id, resumed, reuse_result = _reuse_or_resume_existing_operation(
        ctx,
        existing,
        op_type=op_type,
        resume_error_result=resume_error_result,
        accepted_message=accepted_message,
        expected_target=target,
        target_conflict_result=target_conflict_result,
        log_action=log_action,
    )
    if reuse_result is not None:
        return op_id, resumed, reuse_result

    if not op_id:
        op_id = _new_operation_id(op_type)
        error_result = _create_operation(
            ctx,
            op_type,
            op_id,
            target=target,
            idempotency_key=idempotency_key,
            payload=payload,
            error_result=create_error_result,
        )
        if error_result is not None:
            return "", False, error_result

    return op_id, resumed, None
