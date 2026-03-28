"""Compose control-plane operations from focused use-case modules."""

from types import SimpleNamespace
from typing import Any

from app.services import backup_usecase as _backup
from app.services import restore_execution as _restore_execution
from app.services import restore_jobs as _restore_jobs
from app.services import restore_status as _restore_status
from app.services import restore_workflow_helpers as _restore_helpers
from app.services import start_usecase as _start
from app.services import stop_usecase as _stop

_DIRECT_EXPORTS = {
    "ensure_startup_rcon_settings": _restore_helpers.ensure_startup_rcon_settings,
    "run_elevated_command": _restore_helpers.run_elevated_command,
    "write_session_start_time": _restore_helpers.write_session_start_time,
    "stop_service_runtime": _restore_helpers.stop_service_runtime,
    "restore_world_backup": _restore_execution.restore_world_backup,
    "append_restore_event": _restore_status.append_restore_event,
    "start_restore_job": _restore_jobs.start_restore_job,
    "get_restore_status": _restore_status.get_restore_status,
    "set_service_status_intent": _start.set_service_status_intent,
    "get_service_status_intent": _start.get_service_status_intent,
    "validate_sudo_password": _start.validate_sudo_password,
    "validate_admin_password": _start.validate_admin_password,
    "validate_superadmin_password": _start.validate_superadmin_password,
    "ensure_session_file": _restore_helpers.ensure_session_file,
    "read_session_start_time": _start.read_session_start_time,
    "clear_session_start_time": _restore_helpers.clear_session_start_time,
    "reset_backup_schedule_state": _restore_helpers.reset_backup_schedule_state,
    "get_session_start_time": _start.get_session_start_time,
    "get_session_duration_text": _start.get_session_duration_text,
}

for _name, _target in _DIRECT_EXPORTS.items():
    globals()[_name] = _target

del _name
del _target

is_backup_running = _restore_helpers.is_backup_running
graceful_stop_minecraft = _stop.graceful_stop_minecraft
stop_server_automatically = _stop.stop_server_automatically
get_backup_zip_snapshot = _backup.get_backup_zip_snapshot
backup_snapshot_changed = _backup.backup_snapshot_changed
format_backup_time = _backup.format_backup_time
get_server_time_text = _backup.get_server_time_text
get_latest_backup_zip_timestamp = _backup.get_latest_backup_zip_timestamp
get_backup_schedule_times = _backup.get_backup_schedule_times
get_backup_status = _backup.get_backup_status

# Preserve the patch surface used by tests around process execution.
_calls = SimpleNamespace(
    service_start_no_block=_start._calls.service_start_no_block,
    run_backup_script=_backup._calls.run_backup_script,
)


def start_service_non_blocking(ctx: Any, timeout: float = 12) -> Any:
    _start._calls.service_start_no_block = _calls.service_start_no_block
    return _start.start_service_non_blocking(ctx, timeout=int(timeout))


def run_backup_script(ctx: Any, count_skip_as_success: bool = True, trigger: str = "manual") -> bool:
    _backup._calls.run_backup_script = _calls.run_backup_script
    setattr(_backup, "is_backup_running", is_backup_running)
    result = _backup.run_backup_script(
        ctx,
        count_skip_as_success=count_skip_as_success,
        trigger=trigger,
        snapshot_reader=get_backup_zip_snapshot,
        snapshot_changed_fn=backup_snapshot_changed,
    )
    return bool(result)


__all__ = [
    *_DIRECT_EXPORTS.keys(),
    "start_service_non_blocking",
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
