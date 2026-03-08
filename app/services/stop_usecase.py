"""Stop/shutdown control-plane use cases."""

from app.services.restore_workflow_helpers import (
    clear_session_start_time,
    reset_backup_schedule_state,
    stop_service_systemd,
)
from app.services.start_usecase import set_service_status_intent
from app.services.backup_usecase import run_backup_script


def _publish_transition_metrics(ctx):
    publish_fn = getattr(ctx, "_collect_and_publish_metrics", None) or getattr(ctx, "collect_and_publish_metrics", None)
    if callable(publish_fn):
        try:
            publish_fn()
        except Exception:
            pass


def _invalidate_runtime_status(ctx):
    invalidate_fn = getattr(ctx, "invalidate_status_cache", None)
    if callable(invalidate_fn):
        try:
            invalidate_fn()
        except Exception:
            pass


def graceful_stop_minecraft(ctx, trigger="session_end"):
    """Stop service and run a shutdown backup with the provided trigger."""
    systemd_ok = stop_service_systemd(ctx)
    backup_ok = run_backup_script(ctx, trigger=trigger)
    return {"systemd_ok": systemd_ok, "backup_ok": backup_ok}


def stop_server_automatically(ctx, trigger="session_end"):
    """Apply automatic-stop flow (intent, stop, session clear, backup reset)."""
    set_service_status_intent(ctx, "shutting")
    _invalidate_runtime_status(ctx)
    _publish_transition_metrics(ctx)
    result = graceful_stop_minecraft(ctx, trigger=trigger)
    systemd_ok = bool((result or {}).get("systemd_ok")) if isinstance(result, dict) else bool(result)
    backup_ok = bool((result or {}).get("backup_ok")) if isinstance(result, dict) else True
    if systemd_ok and backup_ok:
        clear_session_start_time(ctx)
        reset_backup_schedule_state(ctx)
    _invalidate_runtime_status(ctx)
    _publish_transition_metrics(ctx)
    return result
