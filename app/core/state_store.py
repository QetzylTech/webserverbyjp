"""SQLite-backed structured state storage helpers.

This module is a facade that preserves the original public API while
delegating to focused submodules by responsibility.
"""

from app.core.state_store_core import initialize_state_db, migrate_state_db_to_data_dir
from app.core.state_store_users_cleanup import (
    upsert_user_record,
    load_fallmap,
    load_cleanup_config,
    save_cleanup_config,
    load_cleanup_history_runs,
    append_cleanup_history_run,
    save_cleanup_history_runs,
)
from app.core.state_store_restore import (
    restore_id_exists,
    append_restore_name_run,
    append_restore_run,
    restore_backup_records_match,
)
from app.core.state_store_files import replace_file_records_snapshot

__all__ = [
    "initialize_state_db",
    "migrate_state_db_to_data_dir",
    "upsert_user_record",
    "load_fallmap",
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
]
