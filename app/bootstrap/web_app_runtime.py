"""Runtime wiring constants for web bootstrap."""

from app.core.filesystem_utils import (
    list_download_files as _list_download_files,
    read_recent_file_lines as _read_recent_file_lines,
    safe_file_mtime_ns as _safe_file_mtime_ns,
    safe_filename_in_dir as _safe_filename_in_dir,
)
from app.ports import ports

RUNTIME_CONTEXT_EXTRA_KEYS = frozenset({
    "APP_DIR",
    "APP_STATE_DB_PATH",
    "STATE",
})

RUNTIME_IMPORTED_SYMBOLS = {
    "_list_download_files": _list_download_files,
    "_read_recent_file_lines": _read_recent_file_lines,
    "_safe_file_mtime_ns": _safe_file_mtime_ns,
    "_safe_filename_in_dir": _safe_filename_in_dir,
    "get_cpu_frequency": ports.metrics.get_cpu_frequency,
    "get_cpu_usage_per_core": ports.metrics.get_cpu_usage_per_core,
    "get_ram_usage": ports.metrics.get_ram_usage,
    "get_storage_usage": ports.metrics.get_storage_usage,
}
