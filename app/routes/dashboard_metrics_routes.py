"""Metrics routes for the shell-first MC web dashboard."""
# mypy: disable-error-code=untyped-decorator

import copy
import json
import threading
import time
from collections.abc import Iterator
from typing import Any, cast

from flask import Response, jsonify, request, stream_with_context

from app.core import state_store as state_store_service
from app.services import client_registry as client_registry_service

_METRICS_ROUTE_CACHE_LOCK = threading.Lock()
# Short cache for /metrics JSON fallback requests. This improves burst behavior,
# but it can add roughly 1 second of visible delay to status transitions when the
# dashboard is reading status through /metrics instead of waiting on the SSE stream.
_METRICS_ROUTE_CACHE_TTL_SECONDS = 1.0
_METRICS_ROUTE_CACHE: dict[str, Any] = {
    "event_id": -1,
    "expires_at": 0.0,
    "payload": None,
    "scope_key": None,
}
_state_store = cast(Any, state_store_service)
_client_registry = cast(Any, client_registry_service)

def _coerce_event_id(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip() or str(default))
        except ValueError:
            return default
    return default


def register_metrics_routes(app: Any, state: dict[str, Any], get_nav_alert_state_from_request: Any = None) -> None:
    """Register metrics JSON and SSE endpoints."""
    runtime_state = getattr(state, "ctx", state)
    process_role = str(state.get("PROCESS_ROLE", "all") or "all").strip().lower()

    def _runtime_get(name: str, default: Any = None) -> Any:
        if isinstance(runtime_state, dict):
            return runtime_state.get(name, default)
        return getattr(runtime_state, name, default)

    def _runtime_set(name: str, value: Any) -> None:
        if isinstance(runtime_state, dict):
            runtime_state[name] = value
            return
        setattr(runtime_state, name, value)

    def _ensure_metrics_runtime_started_best_effort() -> None:
        starter = state.get("ensure_metrics_collector_started")
        if not callable(starter):
            return
        try:
            starter()
        except Exception:
            pass

    def _attach_nav_attention(payload: object) -> object:
        if not isinstance(payload, dict):
            return payload
        get_nav_alert_state = get_nav_alert_state_from_request
        if not callable(get_nav_alert_state):
            return payload
        merged = dict(payload)
        try:
            nav_attention = get_nav_alert_state()
        except Exception:
            nav_attention = {}
        if isinstance(nav_attention, dict) and nav_attention:
            merged["nav_attention"] = dict(nav_attention)
        return merged

    def _latest_metrics_from_db() -> tuple[dict[str, Any] | None, int]:
        db_path = state.get("APP_STATE_DB_PATH")
        if db_path is None:
            return None, 0
        try:
            event = _state_store.get_latest_event(db_path, topic="metrics_snapshot")
        except Exception:
            return None, 0
        if not isinstance(event, dict):
            return None, 0
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None, 0
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, dict):
            return None, 0
        return snapshot, _coerce_event_id(event.get("id", 0))

    def _refresh_metrics_snapshot_best_effort() -> None:
        """Force a fresh metrics snapshot in request-serving roles."""
        _ensure_metrics_runtime_started_best_effort()
        if process_role == "worker":
            return
        publish_fn = state.get("_collect_and_publish_metrics") or state.get("collect_and_publish_metrics")
        if not callable(publish_fn):
            return
        try:
            publish_fn()
        except Exception:
            pass

    @app.route("/metrics")
    def metrics() -> Any:
        """Runtime helper metrics."""
        now = time.time()
        cache_scope_key = id(runtime_state)
        _refresh_metrics_snapshot_best_effort()
        latest_snapshot, latest_event_id = _latest_metrics_from_db()
        with _METRICS_ROUTE_CACHE_LOCK:
            cached_payload = _METRICS_ROUTE_CACHE.get("payload")
            if (
                _METRICS_ROUTE_CACHE.get("scope_key") == cache_scope_key
                and
                _METRICS_ROUTE_CACHE.get("event_id") == int(latest_event_id)
                and float(_METRICS_ROUTE_CACHE.get("expires_at", 0.0) or 0.0) >= now
                and isinstance(cached_payload, dict)
            ):
                return jsonify(copy.deepcopy(cached_payload))
        payload = latest_snapshot if isinstance(latest_snapshot, dict) else state["get_cached_dashboard_metrics"]()
        with _METRICS_ROUTE_CACHE_LOCK:
            _METRICS_ROUTE_CACHE["scope_key"] = cache_scope_key
            _METRICS_ROUTE_CACHE["event_id"] = int(latest_event_id)
            _METRICS_ROUTE_CACHE["expires_at"] = now + _METRICS_ROUTE_CACHE_TTL_SECONDS
            _METRICS_ROUTE_CACHE["payload"] = copy.deepcopy(payload if isinstance(payload, dict) else {})
        return jsonify(payload)

    @app.route("/metrics-stream")
    def metrics_stream() -> Response:
        """Runtime helper metrics_stream."""
        _ensure_metrics_runtime_started_best_effort()
        client_id = str(request.args.get("client_id", "") or request.headers.get("X-MCWEB-Client-Id", "") or "").strip()
        def generate() -> Iterator[str]:
            """Runtime helper generate."""
            def _serialize_snapshot(snapshot: dict[str, Any]) -> str:
                return json.dumps(_attach_nav_attention(snapshot), separators=(",", ":"))

            if client_id:
                _client_registry.register_client(state, client_id, channel="metrics_stream")
            with _runtime_get("metrics_cache_cond"):
                _runtime_set("metrics_stream_client_count", int(_runtime_get("metrics_stream_client_count", 0) or 0) + 1)
                _runtime_get("metrics_cache_cond").notify_all()
            _refresh_metrics_snapshot_best_effort()
            last_event_id = 0
            last_cache_seq = 0
            last_payload = ""
            db_path = state.get("APP_STATE_DB_PATH")
            latest_db_snapshot: dict[str, Any] | None = None
            if db_path is not None:
                try:
                    latest_event = _state_store.get_latest_event(db_path, topic="metrics_snapshot")
                except Exception:
                    latest_event = None
                if isinstance(latest_event, dict):
                    latest_payload = latest_event.get("payload", {})
                    latest_snapshot = latest_payload.get("snapshot") if isinstance(latest_payload, dict) else None
                    last_event_id = _coerce_event_id(latest_event.get("id", 0))
                    if isinstance(latest_snapshot, dict):
                        latest_db_snapshot = latest_snapshot
            with _runtime_get("metrics_cache_cond"):
                cache_payload = dict(_runtime_get("metrics_cache_payload", {})) if isinstance(_runtime_get("metrics_cache_payload", {}), dict) else None
                last_cache_seq = _coerce_event_id(_runtime_get("metrics_cache_seq", 0))
            initial_snapshot = cache_payload if isinstance(cache_payload, dict) else latest_db_snapshot
            if isinstance(initial_snapshot, dict):
                last_payload = _serialize_snapshot(initial_snapshot)
                yield f"data: {last_payload}\n\n"
            try:
                while True:
                    delivered = False
                    with _runtime_get("metrics_cache_cond"):
                        cache_payload = dict(_runtime_get("metrics_cache_payload", {})) if isinstance(_runtime_get("metrics_cache_payload", {}), dict) else None
                        cache_seq = _coerce_event_id(_runtime_get("metrics_cache_seq", 0), last_cache_seq)
                    if isinstance(cache_payload, dict) and cache_seq > last_cache_seq:
                        payload = _serialize_snapshot(cache_payload)
                        if payload != last_payload:
                            yield f"data: {payload}\n\n"
                            delivered = True
                            last_payload = payload
                        last_cache_seq = cache_seq
                    db_path = state.get("APP_STATE_DB_PATH")
                    if db_path is not None:
                        try:
                            rows = _state_store.list_events_since(
                                db_path,
                                topic="metrics_snapshot",
                                since_id=last_event_id,
                                limit=10,
                            )
                        except Exception:
                            rows = []
                        if rows:
                            for row in rows:
                                payload_obj = row.get("payload", {}) if isinstance(row, dict) else {}
                                snapshot = payload_obj.get("snapshot") if isinstance(payload_obj, dict) else None
                                if isinstance(snapshot, dict):
                                    payload = _serialize_snapshot(snapshot)
                                    if payload != last_payload:
                                        yield f"data: {payload}\n\n"
                                        delivered = True
                                        last_payload = payload
                                row_id = _coerce_event_id(
                                    row.get("id", last_event_id) if isinstance(row, dict) else last_event_id,
                                    last_event_id,
                                )
                                last_event_id = max(last_event_id, row_id)
                    if not delivered:
                        yield ": keepalive\n\n"
                    if client_id:
                        _client_registry.touch_client(state, client_id, channel="metrics_stream")
                    configured_heartbeat = float(state["METRICS_STREAM_HEARTBEAT_SECONDS"])
                    heartbeat = max(0.5, min(configured_heartbeat, 1.0))
                    with _runtime_get("metrics_cache_cond"):
                        _runtime_get("metrics_cache_cond").wait_for(
                            lambda: _coerce_event_id(_runtime_get("metrics_cache_seq", 0), last_cache_seq) > last_cache_seq,
                            timeout=heartbeat,
                        )
            finally:
                if client_id:
                    _client_registry.unregister_client(state, client_id, channel="metrics_stream")
                with _runtime_get("metrics_cache_cond"):
                    _runtime_set("metrics_stream_client_count", max(0, int(_runtime_get("metrics_stream_client_count", 0) or 0) - 1))
                    _runtime_get("metrics_cache_cond").notify_all()

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
