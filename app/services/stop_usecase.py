"""Stop/shutdown control-plane use cases."""

from app.services.restore_workflow_helpers import (
    clear_session_start_time,
    reset_backup_schedule_state,
    stop_service_systemd,
)
from app.services.start_usecase import set_service_status_intent
from app.services.backup_usecase import run_backup_script


def graceful_stop_minecraft(ctx, trigger="session_end"):
    """Stop service and run a shutdown backup with the provided trigger."""
    systemd_ok = stop_service_systemd(ctx)
    backup_ok = run_backup_script(ctx, trigger=trigger)
    return {"systemd_ok": systemd_ok, "backup_ok": backup_ok}


def stop_server_automatically(ctx, trigger="session_end"):
    """Apply automatic-stop flow (intent, stop, session clear, backup reset)."""
    set_service_status_intent(ctx, "shutting")
    graceful_stop_minecraft(ctx, trigger=trigger)
    clear_session_start_time(ctx)
    reset_backup_schedule_state(ctx)
