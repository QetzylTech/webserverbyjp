"""Expose structured state-store helpers through one stable compatibility module."""

from app.core import state_store_core as _core
from app.core import state_store_events as _events
from app.core import state_store_files as _files
from app.core import state_store_operations as _operations
from app.core import state_store_restore as _restore
from app.core import state_store_users_cleanup as _users_cleanup

_EXPORT_GROUPS = (
    (
        _core,
        ("initialize_state_db",),
    ),
    (
        _users_cleanup,
        (
            "upsert_user_record",
            "load_fallmap",
            "replace_fallmap",
            "load_cleanup_config",
            "save_cleanup_config",
            "load_cleanup_history_runs",
            "append_cleanup_history_run",
            "save_cleanup_history_runs",
        ),
    ),
    (
        _restore,
        (
            "restore_id_exists",
            "append_restore_name_run",
            "append_restore_run",
            "restore_backup_records_match",
        ),
    ),
    (
        _files,
        ("replace_file_records_snapshot", "load_file_records_snapshot"),
    ),
    (
        _events,
        ("append_event", "list_events_since", "get_latest_event"),
    ),
    (
        _operations,
        (
            "create_operation",
            "update_operation",
            "update_operations_batch",
            "get_operation",
            "get_latest_operation_for_type",
            "list_operations_by_status",
            "get_operation_by_idempotency_key",
        ),
    ),
)

__all__ = []
for _module, _names in _EXPORT_GROUPS:
    for _name in _names:
        globals()[_name] = getattr(_module, _name)
        __all__.append(_name)

del _module
del _name
del _names
