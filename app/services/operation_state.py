"""Shared helpers for operation queue lookups."""

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from app.core import state_store as state_store_service


def _extract_db_path(ctx_or_state: Any) -> str | Path | None:
    if isinstance(ctx_or_state, dict):
        db_path = ctx_or_state.get("APP_STATE_DB_PATH")
        return db_path if isinstance(db_path, (str, Path)) else None
    db_path = getattr(ctx_or_state, "APP_STATE_DB_PATH", None)
    if isinstance(db_path, (str, Path)):
        return db_path
    state = getattr(ctx_or_state, "state", None)
    if isinstance(state, dict):
        nested_db_path = state.get("APP_STATE_DB_PATH")
        return nested_db_path if isinstance(nested_db_path, (str, Path)) else None
    return None


def _extract_stale_seconds(ctx_or_state: Any) -> float:
    value = None
    if isinstance(ctx_or_state, dict):
        value = ctx_or_state.get("OPERATION_INTENT_STALE_SECONDS")
    else:
        value = getattr(ctx_or_state, "OPERATION_INTENT_STALE_SECONDS", None)
        if value is None:
            state = getattr(ctx_or_state, "state", None)
            if isinstance(state, dict):
                value = state.get("OPERATION_INTENT_STALE_SECONDS")
    try:
        parsed = float(value if value is not None else 15.0)
    except Exception:
        parsed = 15.0
    return max(1.0, parsed)


def _operation_is_stale(row: dict[str, Any], stale_seconds: float) -> bool:
    latest_ts = None
    for key in ("started_at", "intent_at"):
        raw = str(row.get(key, "") or "").strip()
        if not raw:
            continue
        try:
            candidate = datetime.fromisoformat(raw).timestamp()
        except Exception:
            continue
        if latest_ts is None or candidate > latest_ts:
            latest_ts = candidate
    if latest_ts is None:
        return False
    return (time.time() - latest_ts) > stale_seconds


def has_pending_operation(ctx_or_state: Any, op_type: object) -> bool:
    db_path = _extract_db_path(ctx_or_state)
    if db_path is None:
        return False
    stale_seconds = _extract_stale_seconds(ctx_or_state)
    try:
        rows = state_store_service.list_operations_by_status(
            db_path,
            statuses=("intent", "in_progress"),
            limit=80,
        )
    except Exception:
        return False
    kind = str(op_type or "").strip().lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("op_type", "") or "").strip().lower() == kind:
            if _operation_is_stale(row, stale_seconds):
                continue
            return True
    return False
