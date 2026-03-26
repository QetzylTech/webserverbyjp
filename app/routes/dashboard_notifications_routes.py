"""Notification SSE routes."""
# mypy: disable-error-code=untyped-decorator

from collections.abc import Iterator, Mapping
import json
import time
from typing import Any

from flask import Response, request, stream_with_context

from app.core import state_store as state_store_service


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


def register_notification_routes(app: Any, state: Mapping[str, Any]) -> None:
    """Register global UI notification SSE stream."""

    @app.route("/notifications-stream")
    def notifications_stream() -> Response:
        explicit_since = request.args.get("since", "") or request.headers.get("Last-Event-ID", "")
        last_event_id = _coerce_event_id(explicit_since)
        if not explicit_since:
            db_path = state.get("APP_STATE_DB_PATH")
            if db_path is not None:
                try:
                    latest_event = state_store_service.get_latest_event(db_path, topic="ui_notification")
                except Exception:
                    latest_event = None
                if isinstance(latest_event, dict):
                    last_event_id = _coerce_event_id(latest_event.get("id"), last_event_id)

        def generate() -> Iterator[str]:
            nonlocal last_event_id
            while True:
                db_path = state.get("APP_STATE_DB_PATH")
                if db_path is not None:
                    try:
                        rows = state_store_service.list_events_since(
                            db_path,
                            topic="ui_notification",
                            since_id=last_event_id,
                            limit=10,
                        )
                    except Exception:
                        rows = []
                    if rows:
                        for row in rows:
                            payload = row.get("payload", {}) if isinstance(row, dict) else {}
                            notification = payload.get("notification") if isinstance(payload, dict) else None
                            row_id = row.get("id", last_event_id) if isinstance(row, dict) else last_event_id
                            last_event_id = _coerce_event_id(row_id, last_event_id)
                            if isinstance(notification, dict):
                                data = json.dumps(notification, separators=(",", ":"))
                                yield f"id: {last_event_id}\nevent: notification\ndata: {data}\n\n"
                        continue
                yield ": keepalive\n\n"
                time.sleep(2.0)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
