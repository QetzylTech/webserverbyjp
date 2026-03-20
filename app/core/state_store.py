"""Expose structured state-store helpers through one stable compatibility module."""

from app.core.state_store_core import initialize_state_db
from app.core.state_store_events import append_event, get_latest_event, list_events_since
from app.core.state_store_files import load_file_records_snapshot, replace_file_records_snapshot
from app.core.state_store_operations import (
    create_operation,
    get_latest_operation_for_type,
    get_operation,
    get_operation_by_idempotency_key,
    list_operations_by_status,
    update_operation,
    update_operations_batch,
)
from app.core.state_store_restore import (
    append_restore_name_run,
    append_restore_run,
    restore_backup_records_match,
    restore_id_exists,
)
from app.core.state_store_users_cleanup import (
    append_cleanup_history_run,
    load_cleanup_config,
    load_cleanup_history_runs,
    load_fallmap,
    replace_fallmap,
    save_cleanup_config,
    save_cleanup_history_runs,
    upsert_user_record,
)

__all__ = [
    "initialize_state_db",
    "upsert_user_record",
    "load_fallmap",
    "replace_fallmap",
    "load_cleanup_config",
    "save_cleanup_config",
    "load_cleanup_history_runs",
    "append_cleanup_history_run",
    "save_cleanup_history_runs",
    "restore_id_exists",
    "append_restore_name_run",
    "append_restore_run",
    "restore_backup_records_match",
    "replace_file_records_snapshot",
    "load_file_records_snapshot",
    "append_event",
    "list_events_since",
    "get_latest_event",
    "create_operation",
    "update_operation",
    "update_operations_batch",
    "get_operation",
    "get_latest_operation_for_type",
    "list_operations_by_status",
    "get_operation_by_idempotency_key",
]
