"""Metrics routes for the shell-first MC web dashboard."""

import copy
import json
import threading
import time

from flask import Response, jsonify, stream_with_context

from app.core import state_store as state_store_service

_METRICS_ROUTE_CACHE_LOCK = threading.Lock()
# Short cache for /metrics JSON fallback requests. This improves burst behavior,
# but it can add roughly 1 second of visible delay to status transitions when the
# dashboard is reading status through /metrics instead of waiting on the SSE stream.
_METRICS_ROUTE_CACHE_TTL_SECONDS = 1.0
_METRICS_ROUTE_CACHE = {
    "event_id": -1,
    "expires_at": 0.0,
    "payload": None,
}


def register_metrics_routes(app, state, get_nav_alert_state_from_request=None):
    """Register metrics JSON and SSE endpoints."""
    process_role = str(state.get("PROCESS_ROLE", "all") or "all").strip().lower()

    def _attach_nav_attention(payload):
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

    def _latest_metrics_from_db():
        db_path = state.get("APP_STATE_DB_PATH")
        if db_path is None:
            return None, 0
        try:
            event = state_store_service.get_latest_event(db_path, topic="metrics_snapshot")
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
        return snapshot, int(event.get("id", 0) or 0)

    def _refresh_metrics_snapshot_best_effort():
        """Force a fresh metrics snapshot in web-only role."""
        if process_role != "web":
            return
        publish_fn = state.get("_collect_and_publish_metrics") or state.get("collect_and_publish_metrics")
        if not callable(publish_fn):
            return
        try:
            publish_fn()
        except Exception:
            pass

    @app.route("/metrics")
    def metrics():
        """Runtime helper metrics."""
        now = time.time()
        _refresh_metrics_snapshot_best_effort()
        latest_snapshot, latest_event_id = _latest_metrics_from_db()
        with _METRICS_ROUTE_CACHE_LOCK:
            cached_payload = _METRICS_ROUTE_CACHE.get("payload")
            if (
                _METRICS_ROUTE_CACHE.get("event_id") == int(latest_event_id)
                and float(_METRICS_ROUTE_CACHE.get("expires_at", 0.0) or 0.0) >= now
                and isinstance(cached_payload, dict)
            ):
                return jsonify(copy.deepcopy(cached_payload))
        payload = latest_snapshot if isinstance(latest_snapshot, dict) else state["get_cached_dashboard_metrics"]()
        with _METRICS_ROUTE_CACHE_LOCK:
            _METRICS_ROUTE_CACHE["event_id"] = int(latest_event_id)
            _METRICS_ROUTE_CACHE["expires_at"] = now + _METRICS_ROUTE_CACHE_TTL_SECONDS
            _METRICS_ROUTE_CACHE["payload"] = copy.deepcopy(payload if isinstance(payload, dict) else {})
        return jsonify(payload)

    @app.route("/metrics-stream")
    def metrics_stream():
        """Runtime helper metrics_stream."""
        def generate():
            """Runtime helper generate."""
            with state["metrics_cache_cond"]:
                state["metrics_stream_client_count"] += 1
                state["metrics_cache_cond"].notify_all()
            last_event_id = 0
            db_path = state.get("APP_STATE_DB_PATH")
            if db_path is not None:
                try:
                    latest_event = state_store_service.get_latest_event(db_path, topic="metrics_snapshot")
                except Exception:
                    latest_event = None
                if isinstance(latest_event, dict):
                    latest_payload = latest_event.get("payload", {})
                    latest_snapshot = latest_payload.get("snapshot") if isinstance(latest_payload, dict) else None
                    last_event_id = int(latest_event.get("id", 0) or 0)
                    if isinstance(latest_snapshot, dict):
                        payload = json.dumps(_attach_nav_attention(latest_snapshot), separators=(",", ":"))
                        yield f"data: {payload}\n\n"
            try:
                while True:
                    _refresh_metrics_snapshot_best_effort()
                    db_path = state.get("APP_STATE_DB_PATH")
                    if db_path is not None:
                        try:
                            rows = state_store_service.list_events_since(
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
                                    payload = json.dumps(_attach_nav_attention(snapshot), separators=(",", ":"))
                                    yield f"data: {payload}\n\n"
                                last_event_id = int(row.get("id", last_event_id) or last_event_id)
                            continue
                    yield ": keepalive\n\n"
                    heartbeat = float(state["METRICS_STREAM_HEARTBEAT_SECONDS"])
                    collect_interval = float(state.get("METRICS_COLLECT_INTERVAL_SECONDS", 1) or 1)
                    time.sleep(min(heartbeat, collect_interval))
            finally:
                with state["metrics_cache_cond"]:
                    state["metrics_stream_client_count"] = max(0, state["metrics_stream_client_count"] - 1)
                    state["metrics_cache_cond"].notify_all()

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
