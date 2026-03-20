"""Shared helpers for operation queue lookups."""

from pathlib import Path
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


def has_pending_operation(ctx_or_state: Any, op_type: object) -> bool:
    db_path = _extract_db_path(ctx_or_state)
    if db_path is None:
        return False
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
            return True
    return False
