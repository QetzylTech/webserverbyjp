"""Notification SSE routes."""

import json
import time

from flask import Response, request, stream_with_context

from app.core import state_store as state_store_service


def register_notification_routes(app, state):
    """Register global UI notification SSE stream."""

    @app.route("/notifications-stream")
    def notifications_stream():
        since_raw = request.args.get("since", "") or request.headers.get("Last-Event-ID", "") or "0"
        try:
            last_event_id = int(str(since_raw).strip() or "0")
        except ValueError:
            last_event_id = 0

        def generate():
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
                            if isinstance(notification, dict):
                                data = json.dumps(notification, separators=(",", ":"))
                                yield f"event: notification\ndata: {data}\n\n"
                            last_event_id = int(row.get("id", last_event_id) or last_event_id)
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
