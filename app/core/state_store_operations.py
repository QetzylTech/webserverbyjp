"""Generic control-plane operation records for async route actions."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

from app.core.state_store_core import _connect, _create_tables
from app.core import profiling

_OPERATIONS_WRITE_LOCK = threading.Lock()
_LATEST_OPERATION_CACHE_LOCK = threading.Lock()
_LATEST_OPERATION_CACHE_TTL_SECONDS = 1.0


DbPath = str | Path
JsonDict = dict[str, object]
UpdateEntry = dict[str, object]
CacheKey = tuple[str, str]


class LatestOperationCacheEntry(TypedDict):
    expires_at: float
    item: JsonDict | None


_LATEST_OPERATION_CACHE: dict[CacheKey, LatestOperationCacheEntry] = {}


def _coerce_dict(raw: object) -> JsonDict | None:
    if not isinstance(raw, dict):
        return None
    return {str(key): value for key, value in raw.items()}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _row_to_operation_payload(row: sqlite3.Row | None) -> JsonDict | None:
    if row is None:
        return None
    try:
        data = _coerce_dict(json.loads(str(row["data_json"] or "{}"))) or {}
    except Exception:
        data = {}
    return {
        "op_id": str(row["op_id"] or ""),
        "op_type": str(row["op_type"] or ""),
        "target": str(row["target"] or ""),
        "idempotency_key": str(row["idempotency_key"] or ""),
        "status": str(row["status"] or ""),
        "checkpoint": str(row["checkpoint"] or ""),
        "attempt": int(row["attempt"] or 1),
        "intent_at": str(row["intent_at"] or ""),
        "started_at": str(row["started_at"] or ""),
        "finished_at": str(row["finished_at"] or ""),
        "error_code": str(row["error_code"] or ""),
        "message": str(row["message"] or ""),
        "data": data,
    }


def _fetch_operation_row(conn: sqlite3.Connection, op_id: object) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT
            op_id,
            op_type,
            target,
            idempotency_key,
            status,
            checkpoint,
            attempt,
            intent_at,
            started_at,
            finished_at,
            error_code,
            message,
            data_json
        FROM operations
        WHERE op_id = ?
        LIMIT 1
        """,
        (str(op_id or ""),),
    ).fetchone()
    return cast(sqlite3.Row | None, row)


def _latest_cache_key(db_path: DbPath, op_type: object) -> CacheKey:
    return (str(db_path), str(op_type))


def _latest_cache_get(db_path: DbPath, op_type: object) -> JsonDict | None:
    now = time.time()
    key = _latest_cache_key(db_path, op_type)
    with _LATEST_OPERATION_CACHE_LOCK:
        cached = _LATEST_OPERATION_CACHE.get(key)
        if cached is None:
            return None
        if cached["expires_at"] < now:
            _LATEST_OPERATION_CACHE.pop(key, None)
            return None
        item = cached["item"]
        return dict(item) if item is not None else None


def _latest_cache_set(db_path: DbPath, op_type: object, item: JsonDict | None) -> None:
    key = _latest_cache_key(db_path, op_type)
    with _LATEST_OPERATION_CACHE_LOCK:
        _LATEST_OPERATION_CACHE[key] = {
            "expires_at": time.time() + _LATEST_OPERATION_CACHE_TTL_SECONDS,
            "item": dict(item) if item is not None else None,
        }


def _latest_cache_invalidate(db_path: DbPath, op_type: object) -> None:
    key = _latest_cache_key(db_path, op_type)
    with _LATEST_OPERATION_CACHE_LOCK:
        _LATEST_OPERATION_CACHE.pop(key, None)


def _serialize_payload(payload: JsonDict) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _build_operation_update_fields(
    *,
    status: object = None,
    error_code: object = None,
    message: object = None,
    started: bool = False,
    finished: bool = False,
    checkpoint: object = None,
    increment_attempt: bool = False,
    payload: object = None,
) -> tuple[list[str], list[object]]:
    fields: list[str] = []
    values: list[object] = []
    if status is not None:
        fields.append("status = ?")
        values.append(str(status))
    if error_code is not None:
        fields.append("error_code = ?")
        values.append(str(error_code))
    if message is not None:
        fields.append("message = ?")
        values.append(str(message))
    if started:
        fields.append("started_at = ?")
        values.append(_now_iso())
    if finished:
        fields.append("finished_at = ?")
        values.append(_now_iso())
    if checkpoint is not None:
        fields.append("checkpoint = ?")
        values.append(str(checkpoint))
    if increment_attempt:
        fields.append("attempt = attempt + 1")
    payload_dict = _coerce_dict(payload)
    if payload_dict is not None:
        fields.append("data_json = ?")
        values.append(_serialize_payload(payload_dict))
    return fields, values


def _record_operation_update(
    db_path: DbPath,
    item: JsonDict | None,
    *,
    checkpoint: object = None,
    previous: JsonDict | None = None,
    fallback_op_id: str = "",
) -> None:
    if item is not None:
        _latest_cache_invalidate(db_path, str(item.get("op_type", "") or ""))
        profiling.record_operation_transition(str(item.get("op_type", "")), item)
        checkpoint_name = checkpoint
        if checkpoint_name is None and previous is not None:
            checkpoint_name = str(item.get("checkpoint", "") or "")
        if checkpoint_name:
            profiling.mark_operation_checkpoint(str(item.get("op_id", "") or fallback_op_id), str(checkpoint_name))
        return
    _latest_cache_invalidate(db_path, "")


def create_operation(
    db_path: DbPath,
    *,
    op_id: object,
    op_type: object,
    target: object = "",
    idempotency_key: object = "",
    status: object = "intent",
    checkpoint: object = "",
    payload: object = None,
) -> JsonDict | None:
    """Insert one operation row and return its persisted payload."""
    with profiling.timed("sqlite.operation.create"):
        return _create_operation_impl(
            db_path,
            op_id=op_id,
            op_type=op_type,
            target=target,
            idempotency_key=idempotency_key,
            status=status,
            checkpoint=checkpoint,
            payload=payload,
        )


def _create_operation_impl(
    db_path: DbPath,
    *,
    op_id: object,
    op_type: object,
    target: object = "",
    idempotency_key: object = "",
    status: object = "intent",
    checkpoint: object = "",
    payload: object = None,
) -> JsonDict | None:
    """Insert one operation row and return its persisted payload."""
    item = _coerce_dict(payload) or {}
    created_at = _now_iso()
    with _OPERATIONS_WRITE_LOCK:
        with _connect(db_path) as conn:
            _create_tables(conn)
            conn.execute(
                """
                INSERT INTO operations (
                    op_id,
                    op_type,
                    target,
                    idempotency_key,
                    status,
                    checkpoint,
                    attempt,
                    intent_at,
                    started_at,
                    finished_at,
                    error_code,
                    message,
                    data_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(op_id or ""),
                    str(op_type or ""),
                    str(target or ""),
                    str(idempotency_key or ""),
                    str(status or "intent"),
                    str(checkpoint or ""),
                    1,
                    created_at,
                    "",
                    "",
                    "",
                    "",
                    _serialize_payload(item),
                ),
            )
            conn.commit()
            row = _fetch_operation_row(conn, op_id)
    saved_item = _row_to_operation_payload(row)
    _latest_cache_invalidate(db_path, str(op_type or ""))
    if saved_item is not None:
        profiling.record_operation_transition(str(saved_item.get("op_type", "") or op_type), saved_item)
        profiling.mark_operation_checkpoint(
            str(saved_item.get("op_id", "") or op_id),
            str(checkpoint or "intent_created"),
        )
    return saved_item


def update_operation(
    db_path: DbPath,
    *,
    op_id: object,
    status: object = None,
    error_code: object = None,
    message: object = None,
    started: bool = False,
    finished: bool = False,
    checkpoint: object = None,
    increment_attempt: bool = False,
    payload: object = None,
) -> JsonDict | None:
    """Update operation state fields for one op_id and return latest row."""
    with profiling.timed("sqlite.operation.update"):
        return _update_operation_impl(
            db_path,
            op_id=op_id,
            status=status,
            error_code=error_code,
            message=message,
            started=started,
            finished=finished,
            checkpoint=checkpoint,
            increment_attempt=increment_attempt,
            payload=payload,
        )


def _update_operation_impl(
    db_path: DbPath,
    *,
    op_id: object,
    status: object = None,
    error_code: object = None,
    message: object = None,
    started: bool = False,
    finished: bool = False,
    checkpoint: object = None,
    increment_attempt: bool = False,
    payload: object = None,
) -> JsonDict | None:
    """Update operation state fields for one op_id and return latest row."""
    previous: JsonDict | None = None
    if profiling.ENABLED:
        previous = get_operation(db_path, op_id)
    fields, values = _build_operation_update_fields(
        status=status,
        error_code=error_code,
        message=message,
        started=started,
        finished=finished,
        checkpoint=checkpoint,
        increment_attempt=increment_attempt,
        payload=payload,
    )
    if not fields:
        return get_operation(db_path, op_id)
    values.append(str(op_id or ""))
    with _OPERATIONS_WRITE_LOCK:
        with _connect(db_path) as conn:
            _create_tables(conn)
            conn.execute(
                f"UPDATE operations SET {', '.join(fields)} WHERE op_id = ?",
                tuple(values),
            )
            conn.commit()
            row = _fetch_operation_row(conn, op_id)
    item = _row_to_operation_payload(row)
    _record_operation_update(db_path, item, checkpoint=checkpoint, previous=previous, fallback_op_id=str(op_id or ""))
    return item


def update_operations_batch(db_path: DbPath, *, updates: object) -> list[JsonDict]:
    """Apply multiple operation updates in one sqlite transaction."""
    entries: list[UpdateEntry] = []
    if isinstance(updates, list):
        for item in updates:
            entry = _coerce_dict(item)
            if entry is not None:
                entries.append(entry)
    if not entries:
        return []
    previous_map: dict[str, JsonDict | None] = {}
    if profiling.ENABLED:
        for item in entries:
            op_id = str(item.get("op_id", "") or "").strip()
            if op_id:
                previous_map[op_id] = get_operation(db_path, op_id)
    touched: list[tuple[JsonDict, object, JsonDict | None]] = []
    with profiling.timed("sqlite.operation.update_batch"):
        with _OPERATIONS_WRITE_LOCK:
            with _connect(db_path) as conn:
                _create_tables(conn)
                for entry in entries:
                    payload = entry
                    op_id = str(payload.get("op_id", "") or "").strip()
                    if not op_id:
                        continue
                    status = payload.get("status")
                    error_code = payload.get("error_code")
                    message = payload.get("message")
                    started = bool(payload.get("started", False))
                    finished = bool(payload.get("finished", False))
                    checkpoint = payload.get("checkpoint")
                    increment_attempt = bool(payload.get("increment_attempt", False))
                    data_payload = payload.get("payload")
                    fields, values = _build_operation_update_fields(
                        status=status,
                        error_code=error_code,
                        message=message,
                        started=started,
                        finished=finished,
                        checkpoint=checkpoint,
                        increment_attempt=increment_attempt,
                        payload=data_payload,
                    )
                    if not fields:
                        continue
                    values.append(op_id)
                    conn.execute(
                        f"UPDATE operations SET {', '.join(fields)} WHERE op_id = ?",
                        tuple(values),
                    )
                    row = _fetch_operation_row(conn, op_id)
                    item = _row_to_operation_payload(row)
                    if item is not None:
                        touched.append((item, checkpoint, previous_map.get(op_id)))
                conn.commit()
    out: list[JsonDict] = []
    for item, checkpoint, previous in touched:
        out.append(item)
        _record_operation_update(db_path, item, checkpoint=checkpoint, previous=previous)
    return out


def get_operation(db_path: DbPath, op_id: object) -> JsonDict | None:
    """Return one operation row as dict or None when missing."""
    with profiling.timed("sqlite.operation.get"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            row = _fetch_operation_row(conn, op_id)
    return _row_to_operation_payload(row)


def get_latest_operation_for_type(db_path: DbPath, op_type: object) -> JsonDict | None:
    """Return most recently updated operation row for one operation type."""
    kind = str(op_type or "").strip()
    if not kind:
        return None
    cached = _latest_cache_get(db_path, kind)
    if isinstance(cached, dict):
        return cached
    with profiling.timed("sqlite.operation.get_latest_by_type"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            row = conn.execute(
                """
                SELECT op_id
                FROM operations
                WHERE op_type = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (kind,),
            ).fetchone()
    if row is None:
        _latest_cache_set(db_path, kind, None)
        return None
    item = get_operation(db_path, str(row["op_id"] or ""))
    _latest_cache_set(db_path, kind, item)
    return item


def list_operations_by_status(db_path: DbPath, *, statuses: object, limit: object = 200) -> list[JsonDict]:
    """Return latest operations filtered by status values."""
    if not isinstance(statuses, (list, tuple, set)):
        return []
    values = [str(item or "").strip() for item in statuses if str(item or "").strip()]
    if not values:
        return []
    max_rows = max(1, _to_int(limit, 200))
    placeholders = ",".join("?" for _ in values)
    with profiling.timed("sqlite.operation.list_by_status"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            rows = conn.execute(
                f"""
                SELECT op_id
                FROM operations
                WHERE status IN ({placeholders})
                ORDER BY id DESC
                LIMIT ?
                """,
                tuple([*values, max_rows]),
            ).fetchall()
    out: list[JsonDict] = []
    for row in rows:
        item = get_operation(db_path, str(row["op_id"] or ""))
        if item is not None:
            out.append(item)
    return out


def get_operation_by_idempotency_key(
    db_path: DbPath,
    *,
    op_type: object,
    idempotency_key: object,
) -> JsonDict | None:
    """Return latest operation by type and idempotency key."""
    kind = str(op_type or "").strip()
    key = str(idempotency_key or "").strip()
    if not kind or not key:
        return None
    with profiling.timed("sqlite.operation.get_by_idempotency"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            row = conn.execute(
                """
                SELECT op_id
                FROM operations
                WHERE op_type = ? AND idempotency_key = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (kind, key),
            ).fetchone()
    if row is None:
        return None
    return get_operation(db_path, str(row["op_id"] or ""))
