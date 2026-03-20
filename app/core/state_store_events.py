"""SQLite-backed append/read helpers for lightweight event streaming."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.state_store_core import _connect, _create_tables
from app.core import profiling


DbPath = str | Path
JsonDict = dict[str, object]


def _coerce_dict(raw: object) -> JsonDict | None:
    if not isinstance(raw, dict):
        return None
    return {str(key): value for key, value in raw.items()}


def _to_int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            return int(value)
    except Exception:
        pass
    return default


def append_event(db_path: DbPath, *, topic: object, payload: object = None) -> int:
    """Append one event row and return the inserted event id."""
    subject = str(topic or "").strip()
    if not subject:
        return 0
    body = _coerce_dict(payload) or {}
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


def list_events_since(db_path: DbPath, *, topic: object, since_id: object = 0, limit: object = 200) -> list[JsonDict]:
    """Return ordered events for one topic after an event id."""
    subject = str(topic or "").strip()
    if not subject:
        return []
    last_id = max(0, _to_int(since_id))
    max_rows = max(1, min(1000, _to_int(limit, 200)))
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
    out: list[JsonDict] = []
    for row in rows:
        try:
            payload = _coerce_dict(json.loads(str(row["payload_json"] or "{}"))) or {}
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


def get_latest_event(db_path: DbPath, *, topic: object) -> JsonDict | None:
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
        payload = _coerce_dict(json.loads(str(row["payload_json"] or "{}"))) or {}
    except Exception:
        payload = {}
    return {
        "id": int(row["id"] or 0),
        "topic": str(row["topic"] or ""),
        "created_at": str(row["created_at"] or ""),
        "payload": payload,
    }
