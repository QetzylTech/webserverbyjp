"""Stop/shutdown control-plane use cases."""

from typing import Any

from app.services.restore_workflow_helpers import (
    clear_session_start_time,
    reset_backup_schedule_state,
    stop_service_runtime,
)
from app.services.start_usecase import set_service_status_intent
from app.services.backup_usecase import run_backup_script


def _publish_transition_metrics(ctx: Any) -> None:
    publish_fn = getattr(ctx, "_collect_and_publish_metrics", None) or getattr(ctx, "collect_and_publish_metrics", None)
    if callable(publish_fn):
        try:
            publish_fn()
        except Exception:
            pass


def _invalidate_runtime_status(ctx: Any) -> None:
    invalidate_fn = getattr(ctx, "invalidate_status_cache", None)
    if callable(invalidate_fn):
        try:
            invalidate_fn()
        except Exception:
            pass


def graceful_stop_minecraft(ctx: Any, trigger: str = "session_end") -> dict[str, bool]:
    """Stop service and run a shutdown backup with the provided trigger."""
    service_stop_ok = stop_service_runtime(ctx)
    backup_ok = run_backup_script(ctx, trigger=trigger)  # type: ignore[no-untyped-call]
    return {"service_stop_ok": service_stop_ok, "backup_ok": backup_ok}


def stop_server_automatically(ctx: Any, trigger: str = "session_end") -> dict[str, bool]:
    """Apply automatic-stop flow (intent, stop, session clear, backup reset)."""
    set_service_status_intent(ctx, "shutting")  # type: ignore[no-untyped-call]
    _invalidate_runtime_status(ctx)
    _publish_transition_metrics(ctx)
    result = graceful_stop_minecraft(ctx, trigger=trigger)
    service_stop_ok = bool((result or {}).get("service_stop_ok")) if isinstance(result, dict) else bool(result)
    backup_ok = bool((result or {}).get("backup_ok")) if isinstance(result, dict) else True
    if service_stop_ok and backup_ok:
        clear_session_start_time(ctx)
        reset_backup_schedule_state(ctx)
    _invalidate_runtime_status(ctx)
    _publish_transition_metrics(ctx)
    return result
