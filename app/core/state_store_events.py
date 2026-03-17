"""SQLite-backed append/read helpers for lightweight event streaming."""

from __future__ import annotations

import json

from app.core.state_store_core import _connect, _create_tables
from app.core import profiling


def append_event(db_path, *, topic, payload=None):
    """Append one event row and return the inserted event id."""
    subject = str(topic or "").strip()
    if not subject:
        return 0
    body = payload if isinstance(payload, dict) else {}
    with profiling.timed("sqlite.events.append"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            cursor = conn.execute(
                """
                INSERT INTO events (topic, payload_json, created_at)
                VALUES (?, ?, datetime('now'))
                """,
                (
                    subject,
                    json.dumps(body, ensure_ascii=True, separators=(",", ":")),
                ),
            )
            conn.commit()
            try:
                return int(cursor.lastrowid or 0)
            except Exception:
                return 0


def list_events_since(db_path, *, topic, since_id=0, limit=200):
    """Return ordered events for one topic after an event id."""
    subject = str(topic or "").strip()
    if not subject:
        return []
    last_id = max(0, int(since_id or 0))
    max_rows = max(1, min(1000, int(limit or 200)))
    with profiling.timed("sqlite.events.list_since"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            rows = conn.execute(
                """
                SELECT id, topic, payload_json, created_at
                FROM events
                WHERE topic = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (subject, last_id, max_rows),
            ).fetchall()
    out = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        out.append(
            {
                "id": int(row["id"] or 0),
                "topic": str(row["topic"] or ""),
                "created_at": str(row["created_at"] or ""),
                "payload": payload,
            }
        )
    return out


def get_latest_event(db_path, *, topic):
    """Return latest event for one topic, or None."""
    subject = str(topic or "").strip()
    if not subject:
        return None
    with profiling.timed("sqlite.events.get_latest"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            row = conn.execute(
                """
                SELECT id, topic, payload_json, created_at
                FROM events
                WHERE topic = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (subject,),
            ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    return {
        "id": int(row["id"] or 0),
        "topic": str(row["topic"] or ""),
        "created_at": str(row["created_at"] or ""),
        "payload": payload,
    }
