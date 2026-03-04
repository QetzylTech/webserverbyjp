"""Generic control-plane operation records for async route actions."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.core.state_store_core import _connect, _create_tables
from app.core import profiling


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def create_operation(
    db_path,
    *,
    op_id,
    op_type,
    target="",
    idempotency_key="",
    status="intent",
    checkpoint="",
    payload=None,
):
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
    db_path,
    *,
    op_id,
    op_type,
    target="",
    idempotency_key="",
    status="intent",
    checkpoint="",
    payload=None,
):
    """Insert one operation row and return its persisted payload."""
    item = payload if isinstance(payload, dict) else {}
    created_at = _now_iso()
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
                json.dumps(item, ensure_ascii=True, separators=(",", ":")),
            ),
        )
        conn.commit()
    item = get_operation(db_path, op_id)
    if isinstance(item, dict):
        profiling.record_operation_transition(str(item.get("op_type", "") or op_type), item)
        profiling.mark_operation_checkpoint(str(item.get("op_id", "") or op_id), str(checkpoint or "intent_created"))
    return item


def update_operation(
    db_path,
    *,
    op_id,
    status=None,
    error_code=None,
    message=None,
    started=False,
    finished=False,
    checkpoint=None,
    increment_attempt=False,
    payload=None,
):
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
    db_path,
    *,
    op_id,
    status=None,
    error_code=None,
    message=None,
    started=False,
    finished=False,
    checkpoint=None,
    increment_attempt=False,
    payload=None,
):
    """Update operation state fields for one op_id and return latest row."""
    previous = None
    if profiling.ENABLED:
        previous = get_operation(db_path, op_id)
    fields = []
    values = []
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
    if isinstance(payload, dict):
        fields.append("data_json = ?")
        values.append(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    if not fields:
        return get_operation(db_path, op_id)
    values.append(str(op_id or ""))
    with _connect(db_path) as conn:
        _create_tables(conn)
        conn.execute(
            f"UPDATE operations SET {', '.join(fields)} WHERE op_id = ?",
            tuple(values),
        )
        conn.commit()
    item = get_operation(db_path, op_id)
    if isinstance(item, dict):
        profiling.record_operation_transition(str(item.get("op_type", "")), item)
        checkpoint_name = checkpoint
        if checkpoint_name is None and isinstance(previous, dict):
            checkpoint_name = str(item.get("checkpoint", "") or "")
        if checkpoint_name:
            profiling.mark_operation_checkpoint(str(item.get("op_id", "") or op_id), str(checkpoint_name))
    return item


def get_operation(db_path, op_id):
    """Return one operation row as dict or None when missing."""
    with profiling.timed("sqlite.operation.get"):
        with _connect(db_path) as conn:
            _create_tables(conn)
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
    if row is None:
        return None
    try:
        data = json.loads(str(row["data_json"] or "{}"))
        if not isinstance(data, dict):
            data = {}
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


def get_latest_operation_for_type(db_path, op_type):
    """Return most recently updated operation row for one operation type."""
    kind = str(op_type or "").strip()
    if not kind:
        return None
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
        return None
    return get_operation(db_path, str(row["op_id"] or ""))


def list_operations_by_status(db_path, *, statuses, limit=200):
    """Return latest operations filtered by status values."""
    values = [str(item or "").strip() for item in (statuses or []) if str(item or "").strip()]
    if not values:
        return []
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
                tuple(values + [int(max(1, limit))]),
            ).fetchall()
    out = []
    for row in rows:
        item = get_operation(db_path, str(row["op_id"] or ""))
        if item is not None:
            out.append(item)
    return out


def get_operation_by_idempotency_key(db_path, *, op_type, idempotency_key):
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
