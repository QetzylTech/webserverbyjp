"""Shared helpers for control-plane command handlers."""
from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from app.core import state_store as state_store_service
from app.core.rate_limit import InMemoryRateLimiter
from app.services.worker_scheduler import WorkerSpec, start_worker

from app.commands.control_types import CommandResult


_CONTROL_RATE_LIMITER = InMemoryRateLimiter()
JsonDict = dict[str, Any]
StateMapping = Mapping[str, Any]


def _response_result(response: object) -> CommandResult:
    return CommandResult(response=response)


def _payload_result(
    payload: JsonDict,
    *,
    status_code: int = 200,
    headers: Mapping[str, Any] | None = None,
) -> CommandResult:
    return CommandResult(payload=payload, status_code=int(status_code), headers=dict(headers) if headers else None)


def _accepted_operation_payload(
    op_id: object,
    *,
    status: str = "intent",
    existing: bool = False,
    resumed: bool = False,
    queued: bool = False,
    message: object = None,
) -> JsonDict:
    payload: JsonDict = {
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


def _accepted_operation_result(
    op_id: object,
    *,
    status: str = "intent",
    existing: bool = False,
    resumed: bool = False,
    queued: bool = False,
    message: object = None,
) -> CommandResult:
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


def enforce_rate_limit(
    ctx: Any,
    route_key: str,
    *,
    client_key: str,
    limit: int,
    window_seconds: float,
) -> CommandResult | None:
    scope = ""
    state = getattr(ctx, "state", None)
    if state is not None:
        try:
            scope = str(state.get("APP_STATE_DB_PATH") or "")
        except Exception:
            scope = ""
    if not scope:
        scope = str(id(state) if state is not None else "")
    key = f"{scope}:{route_key}:{client_key}" if scope else f"{route_key}:{client_key}"
    allowed, retry_after = _CONTROL_RATE_LIMITER.allow(
        key,
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


def _new_operation_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _invalidate_observed_cache(ctx: Any) -> None:
    state: StateMapping = ctx.state
    invalidate_fn = state.get("invalidate_observed_state_cache")
    if callable(invalidate_fn):
        try:
            invalidate_fn()
        except Exception:
            pass


def _publish_metrics_now(ctx: Any) -> None:
    state: StateMapping = ctx.state
    publish_fn = state.get("_collect_and_publish_metrics") or state.get("collect_and_publish_metrics")
    if callable(publish_fn):
        try:
            publish_fn()
        except Exception:
            pass


def _refresh_runtime_status(
    ctx: Any,
    intent: object = None,
    *,
    invalidate_observed: bool = False,
) -> None:
    state: StateMapping = ctx.state
    if invalidate_observed:
        _invalidate_observed_cache(ctx)
    if intent is None:
        state["set_service_status_intent"](None)
    else:
        state["set_service_status_intent"](intent)
    state["invalidate_status_cache"]()
    _publish_metrics_now(ctx)


def _enqueue_control_intent(ctx: Any, op_type: object, op_id: object, *, target: object = "") -> bool:
    state: StateMapping = ctx.state
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


def _find_active_operation(ctx: Any, op_type: object, *, target: object = None) -> JsonDict | None:
    state: StateMapping = ctx.state
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


def _update_operation_record(ctx: Any, op_id: object, log_key: str, **fields: object) -> bool:
    state: StateMapping = ctx.state
    try:
        state_store_service.update_operation(
            state["APP_STATE_DB_PATH"],
            op_id=op_id,
            status=fields.get("status"),
            error_code=fields.get("error_code"),
            message=fields.get("message"),
            started=bool(fields.get("started", False)),
            finished=bool(fields.get("finished", False)),
            checkpoint=fields.get("checkpoint"),
            increment_attempt=bool(fields.get("increment_attempt", False)),
            payload=fields.get("payload"),
        )
        return True
    except Exception as exc:
        state["log_mcweb_exception"](f"update_operation/{log_key}", exc)
        return False


def _load_existing_operation(
    ctx: Any,
    op_type: object,
    idempotency_key: object,
    *,
    error_result: CommandResult,
) -> tuple[JsonDict | None, CommandResult | None]:
    if not idempotency_key:
        return None, None
    state: StateMapping = ctx.state
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


def _resume_operation(
    ctx: Any,
    op_id: object,
    *,
    op_type: str,
    error_result: CommandResult,
) -> tuple[bool, CommandResult | None]:
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


def _create_operation(
    ctx: Any,
    op_type: str,
    op_id: str,
    *,
    target: object,
    idempotency_key: object,
    payload: object,
    error_result: CommandResult,
) -> CommandResult | None:
    state: StateMapping = ctx.state
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
    ctx: Any,
    existing: object,
    *,
    op_type: str,
    resume_error_result: CommandResult,
    accepted_message: object = None,
    accepted_statuses: Iterable[str] = ("intent", "in_progress", "observed"),
    expected_target: object = None,
    target_conflict_result: CommandResult | None = None,
    log_action: str | None = None,
) -> tuple[str, bool, CommandResult | None]:
    if not isinstance(existing, dict):
        return "", False, None
    state: StateMapping = ctx.state
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
    ctx: Any,
    op_type: str,
    op_id: str,
    *,
    target: Callable[..., object],
    thread_error_message: str,
    error_result_builder: Callable[[str], CommandResult],
    on_thread_start_failed: Callable[[], None] | None = None,
) -> CommandResult | None:
    state: StateMapping = ctx.state
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


def _active_operation_response(
    ctx: Any,
    op_type: str,
    *,
    target: object = None,
    log_action: str | None = None,
    message: object = None,
) -> CommandResult | None:
    active = _find_active_operation(ctx, op_type, target=target)
    if not isinstance(active, dict):
        return None
    state: StateMapping = ctx.state
    if log_action:
        state["log_mcweb_action"](log_action)
    return _accepted_operation_result(
        active.get("op_id", ""),
        status=str(active.get("status", "") or "intent"),
        existing=True,
        message=message,
    )


def _prepare_operation(
    ctx: Any,
    op_type: str,
    *,
    target: object,
    payload: object,
    idempotency_key: object,
    active_target: object = None,
    active_message: object = None,
    active_log_action: str | None = None,
    active_conflict_result: CommandResult | None = None,
    accepted_message: object = None,
    log_action: str | None = None,
    load_error_result: CommandResult,
    resume_error_result: CommandResult,
    create_error_result: CommandResult,
    target_conflict_result: CommandResult | None = None,
) -> tuple[str, bool, CommandResult | None]:
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
