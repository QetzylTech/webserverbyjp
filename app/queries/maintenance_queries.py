"""Read-side maintenance query helpers and async state caching."""

from __future__ import annotations

import copy
import threading
import time
from datetime import datetime, timezone
from typing import Any, Mapping, cast

from app.core import state_store as state_store_service
from app.services.maintenance_candidate_scan import _cleanup_active_world_path
from app.services.maintenance_engine import _cleanup_evaluate
from app.services.maintenance_snapshot import _cleanup_state_snapshot
from app.services.maintenance_state_store import (
    _cleanup_data_dir,
    _cleanup_get_scope_view,
    _cleanup_load_config,
    _cleanup_normalize_scope,
)
from app.services.worker_scheduler import WorkerSpec, start_worker

_MAINTENANCE_STATE_CACHE_TTL_SECONDS = 3.0
_MAINTENANCE_STATE_CACHE_LOCK = threading.Lock()
MaintenancePayload = dict[str, Any]
AsyncScopeItem = dict[str, Any]
_MAINTENANCE_STATE_CACHE: dict[str, dict[str, Any]] = {}
_MAINTENANCE_ASYNC_REFRESH_INTERVAL_SECONDS = 2.0
_MAINTENANCE_ASYNC_SCOPE_IDLE_SECONDS = 45.0
_MAINTENANCE_ASYNC_LOCK = threading.Lock()
_MAINTENANCE_ASYNC_STATE: dict[str, Any] = {
    "started": False,
    "state_ref": None,
    "ctx_ref": None,
    "scope_items": {},
}


def _scope_items() -> dict[str, AsyncScopeItem]:
    raw_items = _MAINTENANCE_ASYNC_STATE.setdefault("scope_items", {})
    if not isinstance(raw_items, dict):
        raw_items = {}
        _MAINTENANCE_ASYNC_STATE["scope_items"] = raw_items
    return cast(dict[str, AsyncScopeItem], raw_items)


def normalize_scope(raw_scope: object) -> str:
    return str(_cleanup_normalize_scope(raw_scope))


def _state_cache_get(scope: str) -> MaintenancePayload | None:
    now = time.time()
    with _MAINTENANCE_STATE_CACHE_LOCK:
        item = _MAINTENANCE_STATE_CACHE.get(scope)
        if not isinstance(item, dict):
            return None
        if float(item.get("expires_at", 0.0)) < now:
            _MAINTENANCE_STATE_CACHE.pop(scope, None)
            return None
        payload = item.get("payload")
        return copy.deepcopy(payload) if isinstance(payload, dict) else None


def _state_cache_set(scope: str, payload: Mapping[str, Any] | dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    with _MAINTENANCE_STATE_CACHE_LOCK:
        _MAINTENANCE_STATE_CACHE[scope] = {
            "expires_at": time.time() + _MAINTENANCE_STATE_CACHE_TTL_SECONDS,
            "payload": copy.deepcopy(payload),
        }


def invalidate_state_cache(scope: str | None = None) -> None:
    with _MAINTENANCE_STATE_CACHE_LOCK:
        if scope is None:
            _MAINTENANCE_STATE_CACHE.clear()
        else:
            _MAINTENANCE_STATE_CACHE.pop(scope, None)
    with _MAINTENANCE_ASYNC_LOCK:
        if scope is None:
            _scope_items().clear()
        else:
            _scope_items().pop(scope, None)


def _compute_state_payload(ctx: Any, state: Mapping[str, Any], scope: str) -> MaintenancePayload:
    full_cfg = _cleanup_load_config(ctx)
    cfg = _cleanup_get_scope_view(full_cfg, scope)
    preview = _cleanup_evaluate(ctx, cfg, mode="rule", apply_changes=False, trigger="preview")
    return {
        "ok": True,
        **_cleanup_state_snapshot(ctx, cfg),
        "preview": preview,
        "scope": scope,
        "device_map": state["get_device_name_map"](),
    }


def has_active_maintenance_clients(ctx: Any = None) -> bool:
    now = time.time()
    with _MAINTENANCE_ASYNC_LOCK:
        scope_items = _scope_items()
        for item in scope_items.values():
            if not isinstance(item, dict):
                continue
            last_requested_at = float(item.get("last_requested_at", 0.0) or 0.0)
            if (now - last_requested_at) <= _MAINTENANCE_ASYNC_SCOPE_IDLE_SECONDS:
                return True
    return False


def should_pause_maintenance_refresh(ctx: Any) -> bool:
    service_status = str(ctx.get_status() or "inactive").strip().lower()
    off_states = {str(item or "").strip().lower() for item in getattr(ctx, "OFF_STATES", {"inactive", "failed"})}
    return service_status in off_states and not has_active_maintenance_clients(ctx)


def _payload_from_db(state: Mapping[str, Any], scope: str) -> tuple[MaintenancePayload | None, float]:
    db_path = state.get("APP_STATE_DB_PATH")
    if db_path is None:
        return None, 0.0
    try:
        event = state_store_service.get_latest_event(db_path, topic=f"maintenance_state:{scope}")
    except Exception:
        return None, 0.0
    if not isinstance(event, dict):
        return None, 0.0
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None, 0.0
    snapshot = payload.get("snapshot")
    preview = payload.get("preview")
    if not isinstance(snapshot, dict) or not isinstance(preview, dict):
        return None, 0.0
    data = {
        "ok": True,
        **snapshot,
        "preview": preview,
        "scope": scope,
        "device_map": state["get_device_name_map"](),
    }
    try:
        computed_at = float(str(event.get("id", 0) or 0))
    except Exception:
        computed_at = 0.0
    return data, computed_at


def _payload_with_freshness(
    payload: Mapping[str, Any] | dict[str, Any],
    *,
    computed_at: float,
    refreshing: bool = False,
) -> MaintenancePayload:
    body = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    computed_epoch = float(computed_at or 0.0)
    stale_seconds = max(0.0, time.time() - computed_epoch) if computed_epoch > 0 else 0.0
    body["freshness"] = {
        "computed_at_epoch": computed_epoch,
        "computed_at_iso": datetime.fromtimestamp(computed_epoch, tz=timezone.utc).isoformat() if computed_epoch > 0 else "",
        "stale_seconds": stale_seconds,
        "refreshing": bool(refreshing),
    }
    return body


def _async_worker() -> None:
    while True:
        time.sleep(_MAINTENANCE_ASYNC_REFRESH_INTERVAL_SECONDS)
        with _MAINTENANCE_ASYNC_LOCK:
            state = _MAINTENANCE_ASYNC_STATE.get("state_ref")
            ctx = _MAINTENANCE_ASYNC_STATE.get("ctx_ref")
            scope_items = _scope_items()
            scope_rows = [(key, dict(value)) for key, value in scope_items.items() if isinstance(value, dict)]
        if state is None or ctx is None:
            continue
        if should_pause_maintenance_refresh(ctx):
            continue
        now = time.time()
        for scope, item in scope_rows:
            last_requested_at = float(item.get("last_requested_at", 0.0) or 0.0)
            if (now - last_requested_at) > _MAINTENANCE_ASYNC_SCOPE_IDLE_SECONDS:
                continue
            computed_at = float(item.get("computed_at", 0.0) or 0.0)
            force_refresh = bool(item.get("force_refresh", False))
            stale = (now - computed_at) > _MAINTENANCE_STATE_CACHE_TTL_SECONDS
            if not (force_refresh or stale):
                continue
            with _MAINTENANCE_ASYNC_LOCK:
                current = _MAINTENANCE_ASYNC_STATE["scope_items"].setdefault(scope, {})
                if bool(current.get("refreshing", False)):
                    continue
                current["refreshing"] = True
                if force_refresh:
                    current["force_refresh"] = False
            try:
                payload = _compute_state_payload(ctx, state, scope)
                computed_now = time.time()
                with _MAINTENANCE_ASYNC_LOCK:
                    target = _MAINTENANCE_ASYNC_STATE["scope_items"].setdefault(scope, {})
                    target["payload"] = payload
                    target["computed_at"] = computed_now
                    target["refreshing"] = False
                _state_cache_set(scope, _payload_with_freshness(payload, computed_at=computed_now, refreshing=False))
            except Exception:
                with _MAINTENANCE_ASYNC_LOCK:
                    target = _MAINTENANCE_ASYNC_STATE["scope_items"].setdefault(scope, {})
                    target["refreshing"] = False


def _mark_scope_requested(
    ctx: Any,
    state: Mapping[str, Any],
    scope: str,
    *,
    force_refresh: bool = False,
) -> None:
    with _MAINTENANCE_ASYNC_LOCK:
        _MAINTENANCE_ASYNC_STATE["state_ref"] = state
        _MAINTENANCE_ASYNC_STATE["ctx_ref"] = ctx
        item = _scope_items().setdefault(scope, {})
        item["last_requested_at"] = time.time()
        if force_refresh:
            item["force_refresh"] = True
        if not _MAINTENANCE_ASYNC_STATE["started"]:
            start_worker(
                ctx,
                WorkerSpec(
                    name="maintenance-async-worker",
                    target=_async_worker,
                    interval_source=1.0,
                    stop_signal_name="maintenance_async_worker_stop_event",
                    health_marker="maintenance_async_worker",
                ),
            )
            _MAINTENANCE_ASYNC_STATE["started"] = True


def _get_async_item(scope: str) -> AsyncScopeItem | None:
    with _MAINTENANCE_ASYNC_LOCK:
        item = _scope_items().get(scope)
        return dict(item) if isinstance(item, dict) else None


def _set_async_item(
    scope: str,
    payload: Mapping[str, Any] | dict[str, Any],
    *,
    computed_at: float | None = None,
) -> None:
    with _MAINTENANCE_ASYNC_LOCK:
        item = _scope_items().setdefault(scope, {})
        item["payload"] = copy.deepcopy(payload) if isinstance(payload, dict) else {}
        item["computed_at"] = float(computed_at or time.time())
        item["refreshing"] = False


def get_page_model(ctx: Any, state: Mapping[str, Any], scope: str) -> MaintenancePayload:
    payload = get_state_payload(ctx, state, scope, force_refresh=False)
    return {
        "snapshot": {key: value for key, value in payload.items() if key not in {"ok", "preview", "scope", "device_map", "freshness"}},
        "preview": payload.get("preview", {}) if isinstance(payload.get("preview"), dict) else {},
        "scope": scope,
        "device_map": payload.get("device_map", {}) if isinstance(payload.get("device_map"), dict) else {},
        "active_world": str(_cleanup_active_world_path(ctx) or state["WORLD_DIR"]),
        "backup_dir": str(state["BACKUP_DIR"]),
        "stale_dir": str((_cleanup_data_dir(ctx) / "old_worlds").resolve()),
    }


def get_state_payload(
    ctx: Any,
    state: Mapping[str, Any],
    scope: str,
    *,
    force_refresh: bool = False,
) -> MaintenancePayload:
    _mark_scope_requested(ctx, state, scope, force_refresh=force_refresh)
    if not force_refresh:
        db_payload, _db_id = _payload_from_db(state, scope)
        if isinstance(db_payload, dict):
            response_payload = _payload_with_freshness(db_payload, computed_at=time.time(), refreshing=False)
            _state_cache_set(scope, response_payload)
            return response_payload
    if not force_refresh:
        cached = _state_cache_get(scope)
        if isinstance(cached, dict):
            return cached
    async_item = _get_async_item(scope)
    if isinstance(async_item, dict) and isinstance(async_item.get("payload"), dict):
        computed_at = float(async_item.get("computed_at", 0.0) or 0.0)
        refreshing = bool(async_item.get("refreshing", False))
        payload = _payload_with_freshness(async_item.get("payload", {}), computed_at=computed_at, refreshing=refreshing or force_refresh)
        _state_cache_set(scope, payload)
        return payload
    payload = _compute_state_payload(ctx, state, scope)
    computed_at = time.time()
    _set_async_item(scope, payload, computed_at=computed_at)
    response_payload = _payload_with_freshness(payload, computed_at=computed_at, refreshing=False)
    _state_cache_set(scope, response_payload)
    return response_payload


__all__ = [
    'normalize_scope',
    'invalidate_state_cache',
    'get_page_model',
    'get_state_payload',
    'has_active_maintenance_clients',
    'should_pause_maintenance_refresh',
]
