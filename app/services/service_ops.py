"""Control-plane facade composed from use-case modules."""

from types import SimpleNamespace

from app.services import backup_usecase as _backup
from app.services import restore_usecase as _restore
from app.services import start_usecase as _start
from app.services import stop_usecase as _stop
from app.services import restore_workflow_helpers as _workflow
from app.services.restore_workflow_helpers import is_backup_running

ensure_startup_rcon_settings = _restore.ensure_startup_rcon_settings
run_sudo = _restore.run_sudo
write_session_start_time = _restore.write_session_start_time
stop_service_systemd = _workflow.stop_service_systemd
restore_world_backup = _restore.restore_world_backup
append_restore_event = _restore.append_restore_event
start_restore_job = _restore.start_restore_job
get_restore_status = _restore.get_restore_status

set_service_status_intent = _start.set_service_status_intent
get_service_status_intent = _start.get_service_status_intent
validate_sudo_password = _start.validate_sudo_password
ensure_session_file = _workflow.ensure_session_file
read_session_start_time = _start.read_session_start_time
clear_session_start_time = _workflow.clear_session_start_time
reset_backup_schedule_state = _workflow.reset_backup_schedule_state
get_session_start_time = _start.get_session_start_time
get_session_duration_text = _start.get_session_duration_text

# Backward-compatible patch surface for tests and callers.
_calls = SimpleNamespace(
    service_start_no_block=_start._calls.service_start_no_block,
    run_backup_script=_backup._calls.run_backup_script,
)


def start_service_non_blocking(ctx, timeout=12):
    _start._calls.service_start_no_block = _calls.service_start_no_block
    return _start.start_service_non_blocking(ctx, timeout=timeout)


def graceful_stop_minecraft(ctx, trigger="session_end"):
    return _stop.graceful_stop_minecraft(ctx, trigger=trigger)


def stop_server_automatically(ctx, trigger="session_end"):
    return _stop.stop_server_automatically(ctx, trigger=trigger)


def get_backup_zip_snapshot(ctx):
    return _backup.get_backup_zip_snapshot(ctx)


def backup_snapshot_changed(ctx, before_snapshot, after_snapshot):
    return _backup.backup_snapshot_changed(ctx, before_snapshot, after_snapshot)


def run_backup_script(ctx, count_skip_as_success=True, trigger="manual"):
    _backup._calls.run_backup_script = _calls.run_backup_script
    _backup.is_backup_running = is_backup_running
    return _backup.run_backup_script(
        ctx,
        count_skip_as_success=count_skip_as_success,
        trigger=trigger,
        snapshot_reader=get_backup_zip_snapshot,
        snapshot_changed_fn=backup_snapshot_changed,
    )


def format_backup_time(ctx, timestamp):
    return _backup.format_backup_time(ctx, timestamp)


def get_server_time_text(ctx):
    return _backup.get_server_time_text(ctx)


def get_latest_backup_zip_timestamp(ctx):
    return _backup.get_latest_backup_zip_timestamp(ctx)


def get_backup_schedule_times(ctx, service_status=None):
    return _backup.get_backup_schedule_times(ctx, service_status=service_status)


def get_backup_status(ctx):
    return _backup.get_backup_status(ctx)


__all__ = [
    "ensure_startup_rcon_settings",
    "start_service_non_blocking",
    "run_sudo",
    "write_session_start_time",
    "stop_service_systemd",
    "clear_session_start_time",
    "reset_backup_schedule_state",
    "get_session_start_time",
    "get_session_duration_text",
    "set_service_status_intent",
    "get_service_status_intent",
    "validate_sudo_password",
    "ensure_session_file",
    "read_session_start_time",
    "restore_world_backup",
    "append_restore_event",
    "start_restore_job",
    "get_restore_status",
    "graceful_stop_minecraft",
    "stop_server_automatically",
    "run_backup_script",
    "format_backup_time",
    "get_server_time_text",
    "get_latest_backup_zip_timestamp",
    "get_backup_schedule_times",
    "get_backup_status",
    "get_backup_zip_snapshot",
    "backup_snapshot_changed",
]
