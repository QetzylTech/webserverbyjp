"""Shared helpers for cleanup/backup/restore conflict checks."""

from typing import Any, Mapping

from app.services.operation_state import has_pending_operation
from app.services.restore_status import restore_running_from_getter


def _restore_running(ctx: Any = None, state: Mapping[str, Any] | None = None) -> bool:
    if isinstance(state, Mapping):
        return restore_running_from_getter(state.get("get_restore_status"))
    if ctx is None:
        return False
    return restore_running_from_getter(getattr(ctx, "get_restore_status", None))


def priority_conflict(ctx: Any, *, state: Mapping[str, Any] | None = None) -> str:
    """Return a conflict reason string if cleanup should be blocked."""
    if isinstance(state, Mapping):
        backup_running = state.get("is_backup_running", lambda: False)
        if callable(backup_running) and backup_running():
            return "backup_running"
    elif getattr(ctx, "is_backup_running", None) and ctx.is_backup_running():
        return "backup_running"

    if has_pending_operation(ctx, "backup"):
        return "backup_queued"
    if _restore_running(ctx, state=state):
        return "restore_running"
    if has_pending_operation(ctx, "restore"):
        return "restore_queued"
    return ""
