"""Typed application runtime state container."""

from dataclasses import dataclass
from collections.abc import Iterator, MutableMapping
from typing import Any


@dataclass
class BackupState:
    lock: Any
    run_lock: Any
    periodic_runs: int
    last_error: str


@dataclass
class SessionState:
    session_file: Any
    initialized: bool
    init_lock: Any


@dataclass
class AppState(MutableMapping[str, Any]):
    BACKUP_DIR: Any
    BACKUP_INTERVAL_SECONDS: Any
    BACKUP_LOG_FILE: Any
    BACKUP_SCRIPT: Any
    BACKUP_STATE_FILE: Any
    BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS: Any
    BACKUP_WATCH_INTERVAL_OFF_SECONDS: Any
    CRASH_REPORTS_DIR: Any
    CRASH_STOP_GRACE_SECONDS: Any
    CRASH_STOP_MARKERS: Any
    DISPLAY_TZ: Any
    DOCS_DIR: Any
    DOC_README_URL: Any
    FAVICON_URL: Any
    FILES_TEMPLATE_NAME: Any
    FILE_PAGE_ACTIVE_TTL_SECONDS: Any
    FILE_PAGE_CACHE_REFRESH_SECONDS: Any
    FILE_PAGE_HEARTBEAT_INTERVAL_MS: Any
    HOME_PAGE_ACTIVE_TTL_SECONDS: Any
    HOME_PAGE_HEARTBEAT_INTERVAL_MS: Any
    HTML_TEMPLATE_NAME: Any
    IDLE_CHECK_INTERVAL_ACTIVE_SECONDS: Any
    IDLE_CHECK_INTERVAL_OFF_SECONDS: Any
    IDLE_ZERO_PLAYERS_SECONDS: Any
    LOG_FETCHER_IDLE_SLEEP_SECONDS: Any
    LOG_SOURCE_KEYS: Any
    LOG_STREAM_HEARTBEAT_SECONDS: Any
    MCWEB_ACTION_LOG_FILE: Any
    MC_QUERY_INTERVAL_SECONDS: Any
    METRICS_COLLECT_INTERVAL_OFF_SECONDS: Any
    METRICS_COLLECT_INTERVAL_SECONDS: Any
    METRICS_STREAM_HEARTBEAT_SECONDS: Any
    MINECRAFT_LOGS_DIR: Any
    OFF_STATES: Any
    RCON_HOST: Any
    RCON_STARTUP_FALLBACK_AFTER_SECONDS: Any
    RCON_STARTUP_FALLBACK_INTERVAL_SECONDS: Any
    RCON_STARTUP_READY_PATTERN: Any
    SERVER_PROPERTIES_CANDIDATES: Any
    SERVICE: Any
    backup_state: BackupState
    session_state: SessionState
    SLOW_METRICS_INTERVAL_ACTIVE_SECONDS: Any
    SLOW_METRICS_INTERVAL_OFF_SECONDS: Any
    _append_backup_log_cache_line: Any
    _append_mcweb_log_cache_line: Any
    _append_minecraft_log_cache_line: Any
    _backup_failed_response: Any
    _decrement_log_stream_clients: Any
    _ensure_csrf_token: Any
    _get_cached_backup_log_text: Any
    _get_cached_mcweb_log_text: Any
    _get_cached_minecraft_log_text: Any
    _increment_log_stream_clients: Any
    _list_download_files: Any
    _log_source_settings: Any
    _mark_file_page_client_active: Any
    _mark_home_page_client_active: Any
    _ok_response: Any
    _password_rejected_response: Any
    _rcon_rejected_response: Any
    _read_recent_file_lines: Any
    _refresh_rcon_config: Any
    _run_mcrcon: Any
    _safe_file_mtime_ns: Any
    _safe_filename_in_dir: Any
    _session_write_failed_response: Any
    backup_log_cache_lines: Any
    backup_log_cache_loaded: Any
    backup_log_cache_lock: Any
    backup_log_cache_mtime_ns: Any
    clear_session_start_time: Any
    crash_stop_lock: Any
    crash_stop_timer_active: Any
    ensure_file_page_cache_refresher_started: Any
    ensure_log_stream_fetcher_started: Any
    ensure_session_file: Any
    file_page_cache: Any
    file_page_cache_lock: Any
    file_page_cache_refresher_start_lock: Any
    file_page_cache_refresher_started: Any
    file_page_last_seen: Any
    get_backup_schedule_times: Any
    get_backup_status: Any
    get_cached_dashboard_metrics: Any
    get_cached_file_page_items: Any
    get_cpu_frequency: Any
    get_cpu_usage_per_core: Any
    get_idle_countdown: Any
    get_log_source_text: Any
    get_players_online: Any
    get_ram_usage: Any
    get_server_time_text: Any
    get_service_status_class: Any
    get_service_status_display: Any
    get_service_status_intent: Any
    get_session_duration_text: Any
    get_status: Any
    get_storage_usage: Any
    get_tick_rate: Any
    graceful_stop_minecraft: Any
    home_page_last_seen: Any
    idle_lock: Any
    idle_zero_players_since: Any
    invalidate_status_cache: Any
    is_rcon_enabled: Any
    log_mcweb_action: Any
    log_mcweb_exception: Any
    log_stream_states: Any
    mc_cached_players_online: Any
    mc_cached_tick_rate: Any
    mc_last_query_at: Any
    mc_query_lock: Any
    mcweb_log_cache_lines: Any
    mcweb_log_cache_loaded: Any
    mcweb_log_cache_lock: Any
    mcweb_log_cache_mtime_ns: Any
    metrics_cache_cond: Any
    metrics_cache_payload: Any
    metrics_cache_seq: Any
    metrics_collector_start_lock: Any
    metrics_collector_started: Any
    metrics_stream_client_count: Any
    minecraft_log_cache_lines: Any
    minecraft_log_cache_loaded: Any
    minecraft_log_cache_lock: Any
    rcon_cached_enabled: Any
    rcon_cached_password: Any
    rcon_cached_port: Any
    rcon_config_lock: Any
    rcon_last_config_read_at: Any
    rcon_startup_lock: Any
    rcon_startup_ready: Any
    re: Any
    read_session_start_time: Any
    reset_backup_schedule_state: Any
    run_backup_script: Any
    service_status_intent: Any
    service_status_intent_lock: Any
    set_service_status_intent: Any
    slow_metrics_cache: Any
    slow_metrics_cache_at: Any
    slow_metrics_cache_status: Any
    slow_metrics_lock: Any
    stop_server_automatically: Any
    stop_service_systemd: Any
    validate_sudo_password: Any
    write_session_start_time: Any

    @classmethod
    def from_namespace(cls, namespace: dict[str, Any]) -> "AppState":
        missing = []
        kwargs: dict[str, Any] = {}
        for name in cls.__annotations__.keys():
            if name not in namespace:
                missing.append(name)
            else:
                kwargs[name] = namespace[name]
        if missing:
            raise KeyError(f"Missing state members: {', '.join(missing)}")
        return cls(**kwargs)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self.__annotations__:
            raise KeyError(key)
        setattr(self, key, value)

    def __delitem__(self, key: str) -> None:
        raise TypeError("AppState does not support deleting members")

    def __iter__(self) -> Iterator[str]:
        return iter(self.__annotations__.keys())

    def __len__(self) -> int:
        return len(self.__annotations__)
