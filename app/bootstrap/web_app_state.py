"""Runtime state construction for the web bootstrap."""

from collections import deque
import re
import threading

from app.state import BackupState, SessionState
from app.services.storage_guard import StorageGuard

FAVICON_URL = "https://static.wikia.nocookie.net/logopedia/images/e/e3/Minecraft_Launcher.svg/revision/latest/scale-to-width-down/250?cb=20230616222246"


def build_state(app_config, *, app_dir, display_tz):
    backup_script = app_dir / "scripts" / "backup.sh"
    backup_dir = app_config.backup_dir
    minecraft_root_dir = app_config.minecraft_root_dir
    world_dir = minecraft_root_dir / "config"
    crash_reports_dir = minecraft_root_dir / "crash-reports"
    minecraft_logs_dir = minecraft_root_dir / "logs"
    mcweb_log_dir = app_dir / "logs"
    backup_log_file = mcweb_log_dir / "backup.log"
    restore_log_file = mcweb_log_dir / "restore.log"
    mcweb_action_log_file = mcweb_log_dir / "mcweb_actions.log"
    mcweb_log_file = mcweb_log_dir / "mcweb.log"
    data_dir = app_dir / "data"
    app_state_db_path = data_dir / "app_state.sqlite3"
    docs_dir = app_dir / "doc"
    backup_state_file = data_dir / "state.txt"
    session_file = data_dir / "session.txt"

    maintenance_scope_backup_zip = app_config.maintenance_scope_backup_zip
    maintenance_scope_stale_world_dir = app_config.maintenance_scope_stale_world_dir
    maintenance_scope_old_world_zip = app_config.maintenance_scope_old_world_zip
    maintenance_guard_never_delete_newest_n = 1
    maintenance_guard_never_delete_last_backup = True
    maintenance_guard_protect_active_world = True

    rcon_host = "127.0.0.1"
    rcon_port = 25575
    server_properties_candidates = [
        minecraft_root_dir / "server.properties",
        minecraft_root_dir / "server" / "server.properties",
        app_dir / "server.properties",
        app_dir.parent / "server.properties",
    ]

    backup_interval_hours = app_config.backup_interval_hours
    backup_interval_seconds = max(60, int(backup_interval_hours * 3600))
    idle_zero_players_seconds = app_config.idle_zero_players_seconds
    idle_check_interval_seconds = app_config.idle_check_interval_seconds
    idle_check_interval_active_seconds = app_config.idle_check_interval_active_seconds
    idle_check_interval_off_seconds = app_config.idle_check_interval_off_seconds

    idle_zero_players_since = None
    idle_lock = threading.Lock()
    backup_state = BackupState(
        lock=threading.Lock(),
        run_lock=threading.Lock(),
        periodic_runs=0,
        last_error="",
    )
    session_state = SessionState(
        session_file=session_file,
        initialized=False,
        init_lock=threading.Lock(),
    )
    service_status_intent = None
    service_status_intent_lock = threading.Lock()
    restore_lock = threading.Lock()

    off_states = {"inactive", "failed"}
    log_source_keys = ("minecraft", "backup", "restore", "mcweb", "mcweb_log")

    mc_query_interval_seconds = app_config.mc_query_interval_seconds
    mc_query_lock = threading.Lock()
    mc_last_query_at = 0.0
    mc_cached_players_online = "unknown"
    mc_cached_tick_rate = "unknown"
    rcon_startup_ready = False
    rcon_startup_lock = threading.Lock()
    rcon_startup_ready_pattern = re.compile(
        r"Dedicated server took\s+\d+(?:[.,]\d+)?\s+seconds to load",
        re.IGNORECASE,
    )
    rcon_config_lock = threading.Lock()
    rcon_cached_password = None
    rcon_cached_port = rcon_port
    rcon_cached_enabled = False
    rcon_last_config_read_at = 0.0

    metrics_collect_interval_seconds = app_config.metrics_collect_interval_seconds
    metrics_collect_interval_off_seconds = app_config.metrics_collect_interval_off_seconds
    metrics_idle_storage_refresh_seconds = app_config.metrics_idle_storage_refresh_seconds
    metrics_stream_heartbeat_seconds = app_config.metrics_stream_heartbeat_seconds
    log_stream_heartbeat_seconds = app_config.log_stream_heartbeat_seconds
    log_stream_event_buffer_size = app_config.log_stream_event_buffer_size
    minecraft_log_text_limit = app_config.minecraft_log_text_limit
    backup_log_text_limit = app_config.backup_log_text_limit
    mcweb_log_text_limit = app_config.mcweb_log_text_limit
    mcweb_action_log_text_limit = app_config.mcweb_action_log_text_limit
    minecraft_journal_tail_lines = app_config.minecraft_journal_tail_lines
    minecraft_log_visible_lines = app_config.minecraft_log_visible_lines
    home_page_active_ttl_seconds = app_config.home_page_active_ttl_seconds
    home_page_heartbeat_interval_ms = app_config.home_page_heartbeat_interval_ms
    file_page_cache_refresh_seconds = app_config.file_page_cache_refresh_seconds
    file_page_active_ttl_seconds = app_config.file_page_active_ttl_seconds
    file_page_heartbeat_interval_ms = app_config.file_page_heartbeat_interval_ms
    crash_stop_grace_seconds = app_config.crash_stop_grace_seconds
    backup_watch_interval_active_seconds = app_config.backup_watch_interval_active_seconds
    backup_watch_interval_off_seconds = app_config.backup_watch_interval_off_seconds
    backup_warning_ttl_seconds = app_config.backup_warning_ttl_seconds
    low_storage_available_threshold_percent = app_config.low_storage_available_threshold_percent
    storage_safety_check_interval_active_seconds = app_config.storage_safety_check_interval_active_seconds
    storage_safety_check_interval_off_seconds = app_config.storage_safety_check_interval_off_seconds
    operation_reconcile_interval_seconds = app_config.operation_reconcile_interval_seconds
    operation_intent_stale_seconds = app_config.operation_intent_stale_seconds
    operation_start_timeout_seconds = app_config.operation_start_timeout_seconds
    operation_stop_timeout_seconds = app_config.operation_stop_timeout_seconds
    operation_restore_timeout_seconds = app_config.operation_restore_timeout_seconds
    service_status_cache_active_seconds = app_config.service_status_cache_active_seconds
    service_status_cache_off_seconds = app_config.service_status_cache_off_seconds
    service_status_command_timeout_seconds = app_config.service_status_command_timeout_seconds
    journal_load_timeout_seconds = app_config.journal_load_timeout_seconds
    rcon_startup_journal_timeout_seconds = app_config.rcon_startup_journal_timeout_seconds
    slow_metrics_interval_active_seconds = app_config.slow_metrics_interval_active_seconds
    slow_metrics_interval_off_seconds = app_config.slow_metrics_interval_off_seconds
    log_fetcher_idle_sleep_seconds = app_config.log_fetcher_idle_sleep_seconds
    log_fetcher_idle_poll_seconds = app_config.log_fetcher_idle_poll_seconds
    crash_stop_markers = (
        "Preparing crash report with UUID",
        "This crash report has been saved to:",
    )
    process_role = app_config.process_role
    debug_app_host = app_config.debug_app_host
    debug_app_port = app_config.debug_app_port

    metrics_collector_started = False
    metrics_collector_start_lock = threading.Lock()
    metrics_cache_cond = threading.Condition()
    metrics_cache_seq = 0
    metrics_cache_payload = {}
    metrics_stream_client_count = 0
    home_page_last_seen = 0.0
    service_status_cache_lock = threading.Lock()
    service_status_cache_value_ref = [""]
    service_status_cache_at_ref = [0.0]
    slow_metrics_lock = threading.Lock()
    slow_metrics_cache = {}
    slow_metrics_cache_status = ""
    slow_metrics_cache_at = 0.0
    backup_log_cache_lock = threading.Lock()
    backup_log_cache_lines = deque(maxlen=backup_log_text_limit)
    backup_log_cache_loaded = False
    backup_log_cache_mtime_ns = None
    minecraft_log_cache_lock = threading.Lock()
    minecraft_log_cache_lines = deque(maxlen=minecraft_log_text_limit)
    minecraft_log_cache_loaded = False
    mcweb_log_cache_lock = threading.Lock()
    mcweb_log_cache_lines = deque(maxlen=mcweb_action_log_text_limit)
    mcweb_log_cache_loaded = False
    mcweb_log_cache_mtime_ns = None
    file_page_last_seen = 0.0
    file_page_cache_refresher_started = False
    file_page_cache_refresher_start_lock = threading.Lock()
    operation_reconciler_started = False
    operation_reconciler_start_lock = threading.Lock()
    file_page_cache_lock = threading.Lock()
    file_page_cache = {
        "backups": {"items": [], "updated_at": 0.0},
        "crash_logs": {"items": [], "updated_at": 0.0},
        "minecraft_logs": {"items": [], "updated_at": 0.0},
    }
    crash_stop_lock = threading.Lock()
    crash_stop_timer_active = False
    restore_status_lock = threading.Lock()
    restore_status = {
        "job_id": "",
        "running": False,
        "seq": 0,
        "events": [],
        "result": None,
    }
    backup_warning_lock = threading.Lock()
    backup_warning_seq = 0
    backup_warning_message = ""
    backup_warning_at = 0.0
    storage_emergency_lock = threading.Lock()
    storage_emergency_active = False
    storage_guard = StorageGuard()
    client_registry_lock = threading.Lock()
    client_registry = {}
    device_name_map_lock = threading.Lock()
    device_name_map_cache = {}
    device_name_map_mtime_ns_ref = [None]
    password_throttle_lock = threading.Lock()
    password_throttle_state = {"by_ip": {}}

    log_stream_states = {
        source: {
            "cond": threading.Condition(),
            "seq": 0,
            "events": deque(maxlen=log_stream_event_buffer_size),
            "buffered_lines": deque(maxlen=log_stream_event_buffer_size),
            "file_offset": 0,
            "follow_initialized": False,
            "started": False,
            "lifecycle_lock": threading.Lock(),
            "clients": 0,
            "proc": None,
        }
        for source in log_source_keys
    }

    return {
        "FAVICON_URL": FAVICON_URL,
        "SERVICE": app_config.service,
        "ADMIN_PASSWORD_HASH": app_config.admin_password_hash,
        "BACKUP_SCRIPT": backup_script,
        "BACKUP_DIR": backup_dir,
        "MINECRAFT_ROOT_DIR": minecraft_root_dir,
        "WORLD_DIR": world_dir,
        "CRASH_REPORTS_DIR": crash_reports_dir,
        "MINECRAFT_LOGS_DIR": minecraft_logs_dir,
        "MCWEB_LOG_DIR": mcweb_log_dir,
        "BACKUP_LOG_FILE": backup_log_file,
        "RESTORE_LOG_FILE": restore_log_file,
        "MCWEB_ACTION_LOG_FILE": mcweb_action_log_file,
        "MCWEB_LOG_FILE": mcweb_log_file,
        "DATA_DIR": data_dir,
        "APP_STATE_DB_PATH": app_state_db_path,
        "DOCS_DIR": docs_dir,
        "BACKUP_STATE_FILE": backup_state_file,
        "SESSION_FILE": session_file,
        "DOC_README_URL": app_config.doc_readme_url,
        "DEVICE_MAP_CSV_PATH": app_config.device_map_csv_path,
        "DISPLAY_TZ": display_tz,
        "MAINTENANCE_SCOPE_BACKUP_ZIP": maintenance_scope_backup_zip,
        "MAINTENANCE_SCOPE_STALE_WORLD_DIR": maintenance_scope_stale_world_dir,
        "MAINTENANCE_SCOPE_OLD_WORLD_ZIP": maintenance_scope_old_world_zip,
        "MAINTENANCE_GUARD_NEVER_DELETE_NEWEST_N": maintenance_guard_never_delete_newest_n,
        "MAINTENANCE_GUARD_NEVER_DELETE_LAST_BACKUP": maintenance_guard_never_delete_last_backup,
        "MAINTENANCE_GUARD_PROTECT_ACTIVE_WORLD": maintenance_guard_protect_active_world,
        "RCON_HOST": rcon_host,
        "RCON_PORT": rcon_port,
        "SERVER_PROPERTIES_CANDIDATES": server_properties_candidates,
        "BACKUP_INTERVAL_HOURS": backup_interval_hours,
        "BACKUP_INTERVAL_SECONDS": backup_interval_seconds,
        "IDLE_ZERO_PLAYERS_SECONDS": idle_zero_players_seconds,
        "IDLE_CHECK_INTERVAL_SECONDS": idle_check_interval_seconds,
        "IDLE_CHECK_INTERVAL_ACTIVE_SECONDS": idle_check_interval_active_seconds,
        "IDLE_CHECK_INTERVAL_OFF_SECONDS": idle_check_interval_off_seconds,
        "idle_zero_players_since": idle_zero_players_since,
        "idle_lock": idle_lock,
        "backup_state": backup_state,
        "session_state": session_state,
        "service_status_intent": service_status_intent,
        "service_status_intent_lock": service_status_intent_lock,
        "restore_lock": restore_lock,
        "OFF_STATES": off_states,
        "LOG_SOURCE_KEYS": log_source_keys,
        "MC_QUERY_INTERVAL_SECONDS": mc_query_interval_seconds,
        "mc_query_lock": mc_query_lock,
        "mc_last_query_at": mc_last_query_at,
        "mc_cached_players_online": mc_cached_players_online,
        "mc_cached_tick_rate": mc_cached_tick_rate,
        "rcon_startup_ready": rcon_startup_ready,
        "rcon_startup_lock": rcon_startup_lock,
        "RCON_STARTUP_READY_PATTERN": rcon_startup_ready_pattern,
        "rcon_config_lock": rcon_config_lock,
        "rcon_cached_password": rcon_cached_password,
        "rcon_cached_port": rcon_cached_port,
        "rcon_cached_enabled": rcon_cached_enabled,
        "rcon_last_config_read_at": rcon_last_config_read_at,
        "METRICS_COLLECT_INTERVAL_SECONDS": metrics_collect_interval_seconds,
        "METRICS_COLLECT_INTERVAL_OFF_SECONDS": metrics_collect_interval_off_seconds,
        "METRICS_IDLE_STORAGE_REFRESH_SECONDS": metrics_idle_storage_refresh_seconds,
        "METRICS_STREAM_HEARTBEAT_SECONDS": metrics_stream_heartbeat_seconds,
        "LOG_STREAM_HEARTBEAT_SECONDS": log_stream_heartbeat_seconds,
        "LOG_STREAM_EVENT_BUFFER_SIZE": log_stream_event_buffer_size,
        "MINECRAFT_LOG_TEXT_LIMIT": minecraft_log_text_limit,
        "BACKUP_LOG_TEXT_LIMIT": backup_log_text_limit,
        "MCWEB_LOG_TEXT_LIMIT": mcweb_log_text_limit,
        "MCWEB_ACTION_LOG_TEXT_LIMIT": mcweb_action_log_text_limit,
        "MINECRAFT_JOURNAL_TAIL_LINES": minecraft_journal_tail_lines,
        "MINECRAFT_LOG_VISIBLE_LINES": minecraft_log_visible_lines,
        "HOME_PAGE_ACTIVE_TTL_SECONDS": home_page_active_ttl_seconds,
        "HOME_PAGE_HEARTBEAT_INTERVAL_MS": home_page_heartbeat_interval_ms,
        "FILE_PAGE_CACHE_REFRESH_SECONDS": file_page_cache_refresh_seconds,
        "FILE_PAGE_ACTIVE_TTL_SECONDS": file_page_active_ttl_seconds,
        "FILE_PAGE_HEARTBEAT_INTERVAL_MS": file_page_heartbeat_interval_ms,
        "CRASH_STOP_GRACE_SECONDS": crash_stop_grace_seconds,
        "BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS": backup_watch_interval_active_seconds,
        "BACKUP_WATCH_INTERVAL_OFF_SECONDS": backup_watch_interval_off_seconds,
        "BACKUP_WARNING_TTL_SECONDS": backup_warning_ttl_seconds,
        "LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT": low_storage_available_threshold_percent,
        "STORAGE_SAFETY_CHECK_INTERVAL_ACTIVE_SECONDS": storage_safety_check_interval_active_seconds,
        "STORAGE_SAFETY_CHECK_INTERVAL_OFF_SECONDS": storage_safety_check_interval_off_seconds,
        "OPERATION_RECONCILE_INTERVAL_SECONDS": operation_reconcile_interval_seconds,
        "OPERATION_INTENT_STALE_SECONDS": operation_intent_stale_seconds,
        "OPERATION_START_TIMEOUT_SECONDS": operation_start_timeout_seconds,
        "OPERATION_STOP_TIMEOUT_SECONDS": operation_stop_timeout_seconds,
        "OPERATION_RESTORE_TIMEOUT_SECONDS": operation_restore_timeout_seconds,
        "SERVICE_STATUS_CACHE_ACTIVE_SECONDS": service_status_cache_active_seconds,
        "SERVICE_STATUS_CACHE_OFF_SECONDS": service_status_cache_off_seconds,
        "SERVICE_STATUS_COMMAND_TIMEOUT_SECONDS": service_status_command_timeout_seconds,
        "JOURNAL_LOAD_TIMEOUT_SECONDS": journal_load_timeout_seconds,
        "RCON_STARTUP_JOURNAL_TIMEOUT_SECONDS": rcon_startup_journal_timeout_seconds,
        "SLOW_METRICS_INTERVAL_ACTIVE_SECONDS": slow_metrics_interval_active_seconds,
        "SLOW_METRICS_INTERVAL_OFF_SECONDS": slow_metrics_interval_off_seconds,
        "LOG_FETCHER_IDLE_SLEEP_SECONDS": log_fetcher_idle_sleep_seconds,
        "LOG_FETCHER_IDLE_POLL_SECONDS": log_fetcher_idle_poll_seconds,
        "CRASH_STOP_MARKERS": crash_stop_markers,
        "PROCESS_ROLE": process_role,
        "DEBUG_APP_HOST": debug_app_host,
        "DEBUG_APP_PORT": debug_app_port,
        "metrics_collector_started": metrics_collector_started,
        "metrics_collector_start_lock": metrics_collector_start_lock,
        "metrics_cache_cond": metrics_cache_cond,
        "metrics_cache_seq": metrics_cache_seq,
        "metrics_cache_payload": metrics_cache_payload,
        "metrics_stream_client_count": metrics_stream_client_count,
        "home_page_last_seen": home_page_last_seen,
        "service_status_cache_lock": service_status_cache_lock,
        "service_status_cache_value_ref": service_status_cache_value_ref,
        "service_status_cache_at_ref": service_status_cache_at_ref,
        "slow_metrics_lock": slow_metrics_lock,
        "slow_metrics_cache": slow_metrics_cache,
        "slow_metrics_cache_status": slow_metrics_cache_status,
        "slow_metrics_cache_at": slow_metrics_cache_at,
        "backup_log_cache_lock": backup_log_cache_lock,
        "backup_log_cache_lines": backup_log_cache_lines,
        "backup_log_cache_loaded": backup_log_cache_loaded,
        "backup_log_cache_mtime_ns": backup_log_cache_mtime_ns,
        "minecraft_log_cache_lock": minecraft_log_cache_lock,
        "minecraft_log_cache_lines": minecraft_log_cache_lines,
        "minecraft_log_cache_loaded": minecraft_log_cache_loaded,
        "mcweb_log_cache_lock": mcweb_log_cache_lock,
        "mcweb_log_cache_lines": mcweb_log_cache_lines,
        "mcweb_log_cache_loaded": mcweb_log_cache_loaded,
        "mcweb_log_cache_mtime_ns": mcweb_log_cache_mtime_ns,
        "file_page_last_seen": file_page_last_seen,
        "file_page_cache_refresher_started": file_page_cache_refresher_started,
        "file_page_cache_refresher_start_lock": file_page_cache_refresher_start_lock,
        "operation_reconciler_started": operation_reconciler_started,
        "operation_reconciler_start_lock": operation_reconciler_start_lock,
        "file_page_cache_lock": file_page_cache_lock,
        "file_page_cache": file_page_cache,
        "crash_stop_lock": crash_stop_lock,
        "crash_stop_timer_active": crash_stop_timer_active,
        "restore_status_lock": restore_status_lock,
        "restore_status": restore_status,
        "backup_warning_lock": backup_warning_lock,
        "backup_warning_seq": backup_warning_seq,
        "backup_warning_message": backup_warning_message,
        "backup_warning_at": backup_warning_at,
        "storage_emergency_lock": storage_emergency_lock,
        "storage_emergency_active": storage_emergency_active,
        "storage_guard": storage_guard,
        "client_registry_lock": client_registry_lock,
        "client_registry": client_registry,
        "device_name_map_lock": device_name_map_lock,
        "device_name_map_cache": device_name_map_cache,
        "device_name_map_mtime_ns_ref": device_name_map_mtime_ns_ref,
        "password_throttle_lock": password_throttle_lock,
        "password_throttle_state": password_throttle_state,
        "log_stream_states": log_stream_states,
        "APP_DIR": app_dir,
        "APP_CONFIG": app_config,
        "re": re,
    }
