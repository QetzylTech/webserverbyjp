"""Typed application runtime state container."""
from dataclasses import dataclass
from collections.abc import Iterator, MutableMapping
from typing import Any


@dataclass
class BackupState:
    """Mutable backup execution state shared by control and watcher flows."""
    lock: Any
    run_lock: Any
    periodic_runs: int
    last_error: str


@dataclass
class SessionState:
    """Session tracking lifecycle state for one app process."""
    session_file: Any
    initialized: bool
    init_lock: Any


_STATE_CORE_KEYS = (
    "BACKUP_DIR",
    "BACKUP_INTERVAL_SECONDS",
    "BACKUP_LOG_FILE",
    "BACKUP_SCRIPT",
    "BACKUP_STATE_FILE",
    "BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS",
    "BACKUP_WATCH_INTERVAL_OFF_SECONDS",
    "BACKUP_WARNING_TTL_SECONDS",
    "CRASH_REPORTS_DIR",
    "CRASH_STOP_GRACE_SECONDS",
    "CRASH_STOP_MARKERS",
    "DEV_ENABLED",
    "DEBUG_ENABLED",
    "DEBUG_PAGE_VISIBLE",
    "DEBUG_SERVER_PROPERTIES_KEYS",
    "DEBUG_PAGE_LOG_FILE",
    "DISPLAY_TZ",
    "DOCS_DIR",
    "DOC_README_URL",
    "DEVICE_MAP_CSV_PATH",
    "FAVICON_URL",
    "FILES_TEMPLATE_NAME",
    "FILE_PAGE_ACTIVE_TTL_SECONDS",
    "FILE_PAGE_CACHE_REFRESH_SECONDS",
    "FILE_PAGE_HEARTBEAT_INTERVAL_MS",
    "HOME_PAGE_ACTIVE_TTL_SECONDS",
    "HOME_PAGE_HEARTBEAT_INTERVAL_MS",
    "HTML_TEMPLATE_NAME",
    "IDLE_CHECK_INTERVAL_ACTIVE_SECONDS",
    "IDLE_CHECK_INTERVAL_OFF_SECONDS",
    "IDLE_ZERO_PLAYERS_SECONDS",
    "LOG_FETCHER_IDLE_SLEEP_SECONDS",
    "LOG_SOURCE_KEYS",
    "LOG_STREAM_HEARTBEAT_SECONDS",
    "LOG_STREAM_EVENT_BUFFER_SIZE",
    "MINECRAFT_LOG_TEXT_LIMIT",
    "BACKUP_LOG_TEXT_LIMIT",
    "MCWEB_LOG_TEXT_LIMIT",
    "MCWEB_ACTION_LOG_TEXT_LIMIT",
    "MINECRAFT_JOURNAL_TAIL_LINES",
    "MINECRAFT_LOG_VISIBLE_LINES",
    "LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT",
    "MCWEB_ACTION_LOG_FILE",
    "MCWEB_LOG_FILE",
    "MC_QUERY_INTERVAL_SECONDS",
    "SERVICE_STATUS_CACHE_ACTIVE_SECONDS",
    "SERVICE_STATUS_CACHE_OFF_SECONDS",
    "SERVICE_STATUS_COMMAND_TIMEOUT_SECONDS",
    "JOURNAL_LOAD_TIMEOUT_SECONDS",
    "RCON_STARTUP_JOURNAL_TIMEOUT_SECONDS",
    "METRICS_COLLECT_INTERVAL_OFF_SECONDS",
    "METRICS_COLLECT_INTERVAL_SECONDS",
    "METRICS_STREAM_HEARTBEAT_SECONDS",
    "MINECRAFT_LOGS_DIR",
    "MAINTENANCE_SCOPE_BACKUP_ZIP",
    "MAINTENANCE_SCOPE_STALE_WORLD_DIR",
    "MAINTENANCE_SCOPE_OLD_WORLD_ZIP",
    "MAINTENANCE_GUARD_NEVER_DELETE_NEWEST_N",
    "MAINTENANCE_GUARD_NEVER_DELETE_LAST_BACKUP",
    "MAINTENANCE_GUARD_PROTECT_ACTIVE_WORLD",
    "OFF_STATES",
    "RCON_HOST",
    "RCON_STARTUP_FALLBACK_AFTER_SECONDS",
    "RCON_STARTUP_FALLBACK_INTERVAL_SECONDS",
    "RCON_STARTUP_READY_PATTERN",
    "SERVER_PROPERTIES_CANDIDATES",
    "SERVICE",
    "ADMIN_PASSWORD_HASH",
    "WORLD_DIR",
    "USERS_FILE",
    "backup_state",
    "backup_warning_at",
    "backup_warning_lock",
    "backup_warning_message",
    "backup_warning_seq",
    "session_state",
    "SLOW_METRICS_INTERVAL_ACTIVE_SECONDS",
    "SLOW_METRICS_INTERVAL_OFF_SECONDS",
    "STORAGE_SAFETY_CHECK_INTERVAL_ACTIVE_SECONDS",
    "STORAGE_SAFETY_CHECK_INTERVAL_OFF_SECONDS",
)

_STATE_BINDING_KEYS = (
    "_append_backup_log_cache_line",
    "_append_mcweb_log_cache_line",
    "_append_minecraft_log_cache_line",
    "_backup_failed_response",
    "_decrement_log_stream_clients",
    "_ensure_csrf_token",
    "_get_cached_backup_log_text",
    "_get_cached_mcweb_log_text",
    "_get_cached_minecraft_log_text",
    "_increment_log_stream_clients",
    "_list_download_files",
    "_log_source_settings",
    "_mark_file_page_client_active",
    "_mark_home_page_client_active",
    "_ok_response",
    "_low_storage_blocked_response",
    "_password_rejected_response",
    "_rcon_rejected_response",
    "_read_recent_file_lines",
    "_refresh_rcon_config",
    "_run_mcrcon",
    "_safe_file_mtime_ns",
    "_safe_filename_in_dir",
    "_session_write_failed_response",
    "_start_failed_response",
    "apply_debug_env_overrides",
    "backup_log_cache_lines",
    "backup_log_cache_loaded",
    "backup_log_cache_lock",
    "backup_log_cache_mtime_ns",
    "clear_session_start_time",
    "crash_stop_lock",
    "crash_stop_timer_active",
    "debug_env_lock",
    "debug_env_original_values",
    "debug_env_overrides",
    "debug_explorer_list",
    "debug_run_backup",
    "debug_schedule_backup",
    "debug_start_service",
    "debug_stop_service",
    "device_name_map_lock",
    "device_name_map_cache",
    "device_name_map_mtime_ns_ref",
    "ensure_file_page_cache_refresher_started",
    "ensure_log_stream_fetcher_started",
    "ensure_session_file",
    "file_page_cache",
    "file_page_cache_lock",
    "file_page_cache_refresher_start_lock",
    "file_page_cache_refresher_started",
    "file_page_last_seen",
    "get_backup_schedule_times",
    "get_backup_status",
    "get_backup_warning_state",
    "get_cached_dashboard_metrics",
    "get_cached_file_page_items",
    "get_cpu_frequency",
    "get_cpu_usage_per_core",
    "get_debug_env_rows",
    "get_debug_server_properties_rows",
    "get_idle_countdown",
    "get_log_source_text",
    "get_device_name_map",
    "get_players_online",
    "get_ram_usage",
    "get_server_time_text",
    "get_service_status_class",
    "get_service_status_display",
    "get_service_status_intent",
    "get_session_duration_text",
    "get_status",
    "get_storage_usage",
    "get_storage_available_percent",
    "get_tick_rate",
    "get_world_name",
    "graceful_stop_minecraft",
    "home_page_last_seen",
    "idle_lock",
    "idle_zero_players_since",
    "invalidate_status_cache",
    "is_rcon_enabled",
    "log_mcweb_action",
    "log_mcweb_log",
    "log_mcweb_exception",
    "log_debug_page_action",
    "log_stream_states",
    "mc_cached_players_online",
    "mc_cached_tick_rate",
    "mc_last_query_at",
    "mc_query_lock",
    "mcweb_log_cache_lines",
    "mcweb_log_cache_loaded",
    "mcweb_log_cache_lock",
    "mcweb_log_cache_mtime_ns",
    "metrics_cache_cond",
    "metrics_cache_payload",
    "metrics_cache_seq",
    "metrics_collector_start_lock",
    "metrics_collector_started",
    "metrics_stream_client_count",
    "minecraft_log_cache_lines",
    "minecraft_log_cache_loaded",
    "minecraft_log_cache_lock",
    "rcon_cached_enabled",
    "rcon_cached_password",
    "rcon_cached_port",
    "rcon_config_lock",
    "rcon_last_config_read_at",
    "rcon_startup_lock",
    "rcon_startup_ready",
    "re",
    "read_session_start_time",
    "reset_backup_schedule_state",
    "reset_all_debug_overrides",
    "restore_lock",
    "restore_status_lock",
    "restore_status",
    "restore_world_backup",
    "start_restore_job",
    "get_restore_status",
    "start_undo_restore_job",
    "append_restore_event",
    "run_backup_script",
    "set_backup_warning",
    "service_status_cache_lock",
    "service_status_cache_value_ref",
    "service_status_cache_at_ref",
    "service_status_intent",
    "service_status_intent_lock",
    "set_service_status_intent",
    "set_debug_server_properties_values",
    "slow_metrics_cache",
    "slow_metrics_cache_at",
    "slow_metrics_cache_status",
    "slow_metrics_lock",
    "stop_server_automatically",
    "storage_emergency_active",
    "storage_emergency_lock",
    "is_storage_low",
    "low_storage_error_message",
    "start_storage_safety_watcher",
    "stop_service_systemd",
    "users_file_lock",
    "validate_sudo_password",
    "write_session_start_time",
    "record_successful_password_ip",
)

REQUIRED_STATE_KEYS = _STATE_CORE_KEYS + _STATE_BINDING_KEYS
REQUIRED_STATE_KEY_SET = frozenset(REQUIRED_STATE_KEYS)


class AppState(MutableMapping[str, Any]):
    """Strict runtime mapping with attribute and dict-style access."""

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]):
        missing = [key for key in REQUIRED_STATE_KEYS if key not in data]
        if missing:
            raise KeyError(f"Missing state members: {', '.join(missing)}")
        self._data = {key: data[key] for key in REQUIRED_STATE_KEYS}

    @classmethod
    def from_namespace(cls, namespace: dict[str, Any]) -> "AppState":
        """Build AppState from a runtime namespace dictionary."""
        data = {}
        for key in REQUIRED_STATE_KEYS:
            if key in namespace:
                data[key] = namespace[key]
        return cls(data)

    def __getitem__(self, key: str) -> Any:
        """Dunder method __getitem__."""
        try:
            return self._data[key]
        except KeyError as exc:
            raise KeyError(key) from exc

    def __setitem__(self, key: str, value: Any) -> None:
        """Dunder method __setitem__."""
        if key not in REQUIRED_STATE_KEY_SET:
            raise KeyError(key)
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        """Dunder method __delitem__."""
        raise TypeError("AppState does not support deleting members")

    def __iter__(self) -> Iterator[str]:
        """Dunder method __iter__."""
        return iter(REQUIRED_STATE_KEYS)

    def __len__(self) -> int:
        """Dunder method __len__."""
        return len(REQUIRED_STATE_KEYS)

    def __getattr__(self, name: str) -> Any:
        """Support attribute-style state reads used across services."""
        if name in REQUIRED_STATE_KEY_SET:
            return self._data[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Support attribute-style state writes for known keys only."""
        if name == "_data":
            object.__setattr__(self, name, value)
            return
        if name in REQUIRED_STATE_KEY_SET:
            self._data[name] = value
            return
        raise AttributeError(name)

