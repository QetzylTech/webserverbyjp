"""Global UI notification helpers."""

import time

from app.core import state_store as state_store_service


def publish_ui_notification(ctx, payload):
    db_path = getattr(ctx, "APP_STATE_DB_PATH", None)
    if db_path is None:
        return 0
    body = dict(payload) if isinstance(payload, dict) else {}
    body.setdefault("at", time.time())
    try:
        return int(
            state_store_service.append_event(
                db_path,
                topic="ui_notification",
                payload={"notification": body},
            )
            or 0
        )
    except Exception:
        return 0
