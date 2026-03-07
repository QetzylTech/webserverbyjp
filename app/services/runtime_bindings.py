"""Build delegate runtime callables for main.py state wiring."""
import re
import time


def build_runtime_bindings(
    namespace,
    *,
    dashboard_runtime_service,
    control_plane_service,
    session_store_service,
    minecraft_runtime_service,
    session_watchers_service,
):
    # Return mapping of delegate callables bound to namespace state.
    ns = namespace

    def _state():
        return ns["STATE"].ctx

    def _ctx_delegate(service, method_name):
        method = getattr(service, method_name)

        def bound(*args, **kwargs):
            return method(_state(), *args, **kwargs)

        return bound

    def _session_delegate(method_name):
        method = getattr(session_store_service, method_name)

        def bound(*args, **kwargs):
            return method(control_plane_service, _state(), *args, **kwargs)

        return bound

    def _plain_delegate(service, method_name):
        method = getattr(service, method_name)

        def bound(*args, **kwargs):
            return method(*args, **kwargs)

        return bound

    # State-bound delegates with no extra logic.
    _mark_file_page_client_active = _ctx_delegate(dashboard_runtime_service, "mark_file_page_client_active")
    get_cached_file_page_items = _ctx_delegate(dashboard_runtime_service, "get_cached_file_page_items")
    file_page_cache_refresher_loop = _ctx_delegate(dashboard_runtime_service, "file_page_cache_refresher_loop")
    ensure_file_page_cache_refresher_started = _ctx_delegate(dashboard_runtime_service, "ensure_file_page_cache_refresher_started")
    warm_file_page_caches = _ctx_delegate(dashboard_runtime_service, "warm_file_page_caches")

    set_service_status_intent = _ctx_delegate(control_plane_service, "set_service_status_intent")
    get_service_status_intent = _ctx_delegate(control_plane_service, "get_service_status_intent")
    stop_service_systemd = _ctx_delegate(control_plane_service, "stop_service_systemd")
    run_sudo = _ctx_delegate(control_plane_service, "run_sudo")
    validate_sudo_password = _ctx_delegate(control_plane_service, "validate_sudo_password")

    ensure_session_file = _session_delegate("ensure_session_file")
    read_session_start_time = _session_delegate("read_session_start_time")
    write_session_start_time = _session_delegate("write_session_start_time")
    clear_session_start_time = _session_delegate("clear_session_start_time")
    get_session_start_time = _session_delegate("get_session_start_time")
    get_session_duration_text = _session_delegate("get_session_duration_text")

    _log_source_settings = _ctx_delegate(minecraft_runtime_service, "log_source_settings")
    get_log_source_text = _ctx_delegate(minecraft_runtime_service, "get_log_source_text")
    ensure_log_stream_fetcher_started = _ctx_delegate(minecraft_runtime_service, "ensure_log_stream_fetcher_started")
    _increment_log_stream_clients = _ctx_delegate(minecraft_runtime_service, "increment_log_stream_clients")
    _decrement_log_stream_clients = _ctx_delegate(minecraft_runtime_service, "decrement_log_stream_clients")
    _refresh_rcon_config = _ctx_delegate(minecraft_runtime_service, "refresh_rcon_config")
    is_rcon_enabled = _ctx_delegate(minecraft_runtime_service, "is_rcon_enabled")
    is_rcon_startup_ready = _ctx_delegate(minecraft_runtime_service, "is_rcon_startup_ready")
    _run_mcrcon = _ctx_delegate(minecraft_runtime_service, "run_mcrcon")
    _probe_minecraft_runtime_metrics = _ctx_delegate(minecraft_runtime_service, "probe_minecraft_runtime_metrics")
    get_players_online = _ctx_delegate(minecraft_runtime_service, "get_players_online")
    get_tick_rate = _ctx_delegate(minecraft_runtime_service, "get_tick_rate")
    get_service_status_display = _ctx_delegate(minecraft_runtime_service, "get_service_status_display")
    get_service_status_class = _plain_delegate(minecraft_runtime_service, "get_service_status_class")

    get_backups_status = _ctx_delegate(dashboard_runtime_service, "get_backups_status")
    get_cpu_per_core_items = _ctx_delegate(dashboard_runtime_service, "get_cpu_per_core_items")
    get_ram_usage_class = _ctx_delegate(dashboard_runtime_service, "get_ram_usage_class")
    get_storage_usage_class = _ctx_delegate(dashboard_runtime_service, "get_storage_usage_class")
    get_cpu_frequency_class = _ctx_delegate(dashboard_runtime_service, "get_cpu_frequency_class")
    collect_dashboard_metrics = _ctx_delegate(dashboard_runtime_service, "collect_dashboard_metrics")
    get_observed_state = _ctx_delegate(dashboard_runtime_service, "get_observed_state")
    invalidate_observed_state_cache = _ctx_delegate(dashboard_runtime_service, "invalidate_observed_state_cache")
    get_consistency_report = _ctx_delegate(dashboard_runtime_service, "get_consistency_report")
    _mark_home_page_client_active = _ctx_delegate(dashboard_runtime_service, "mark_home_page_client_active")
    _collect_and_publish_metrics = _ctx_delegate(dashboard_runtime_service, "collect_and_publish_metrics")
    metrics_collector_loop = _ctx_delegate(dashboard_runtime_service, "metrics_collector_loop")
    ensure_metrics_collector_started = _ctx_delegate(dashboard_runtime_service, "ensure_metrics_collector_started")
    start_operation_reconciler = _ctx_delegate(dashboard_runtime_service, "start_operation_reconciler")
    get_cached_dashboard_metrics = _ctx_delegate(dashboard_runtime_service, "get_cached_dashboard_metrics")

    graceful_stop_minecraft = _ctx_delegate(control_plane_service, "graceful_stop_minecraft")
    stop_server_automatically = _ctx_delegate(control_plane_service, "stop_server_automatically")
    ensure_startup_rcon_settings = _ctx_delegate(control_plane_service, "ensure_startup_rcon_settings")
    start_service_non_blocking = _ctx_delegate(control_plane_service, "start_service_non_blocking")
    run_backup_script = _ctx_delegate(control_plane_service, "run_backup_script")
    restore_world_backup = _ctx_delegate(control_plane_service, "restore_world_backup")
    append_restore_event = _ctx_delegate(control_plane_service, "append_restore_event")
    start_restore_job = _ctx_delegate(control_plane_service, "start_restore_job")
    get_restore_status = _ctx_delegate(control_plane_service, "get_restore_status")
    format_backup_time = _ctx_delegate(control_plane_service, "format_backup_time")
    get_server_time_text = _ctx_delegate(control_plane_service, "get_server_time_text")
    get_latest_backup_zip_timestamp = _ctx_delegate(control_plane_service, "get_latest_backup_zip_timestamp")
    get_backup_zip_snapshot = _ctx_delegate(control_plane_service, "get_backup_zip_snapshot")
    backup_snapshot_changed = _ctx_delegate(control_plane_service, "backup_snapshot_changed")
    get_backup_schedule_times = _ctx_delegate(control_plane_service, "get_backup_schedule_times")
    get_backup_status = _ctx_delegate(control_plane_service, "get_backup_status")
    is_backup_running = _ctx_delegate(control_plane_service, "is_backup_running")
    reset_backup_schedule_state = _ctx_delegate(control_plane_service, "reset_backup_schedule_state")

    format_countdown = _plain_delegate(session_watchers_service, "format_countdown")
    get_idle_countdown = _ctx_delegate(session_watchers_service, "get_idle_countdown")
    idle_player_watcher = _ctx_delegate(session_watchers_service, "idle_player_watcher")
    start_idle_player_watcher = _ctx_delegate(session_watchers_service, "start_idle_player_watcher")
    backup_session_watcher = _ctx_delegate(session_watchers_service, "backup_session_watcher")
    start_backup_session_watcher = _ctx_delegate(session_watchers_service, "start_backup_session_watcher")
    storage_safety_watcher = _ctx_delegate(session_watchers_service, "storage_safety_watcher")
    start_storage_safety_watcher = _ctx_delegate(session_watchers_service, "start_storage_safety_watcher")
    initialize_session_tracking = _ctx_delegate(session_watchers_service, "initialize_session_tracking")
    _status_state_note = _ctx_delegate(session_watchers_service, "status_state_note")

    def get_storage_used_percent(storage_usage_text=None):
        usage_text = storage_usage_text if storage_usage_text is not None else ns["get_storage_usage"]()
        match = re.search(r"\(([\d.]+)%\)", usage_text or "")
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def get_storage_available_percent(storage_usage_text=None):
        used = get_storage_used_percent(storage_usage_text)
        if used is None:
            return None
        return max(0.0, 100.0 - used)

    def is_storage_low(storage_usage_text=None):
        available = get_storage_available_percent(storage_usage_text)
        if available is None:
            return False
        return available < ns["LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT"]

    def low_storage_error_message(storage_usage_text=None):
        usage_text = storage_usage_text if storage_usage_text is not None else ns["get_storage_usage"]()
        available = get_storage_available_percent(usage_text)
        available_text = "unknown"
        if available is not None:
            available_text = f"{available:.1f}%"
        return (
            f"Low storage space: only {available_text} free ({usage_text}). "
            f"Starting is blocked below {ns['LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT']:.0f}% free."
        )

    def set_backup_warning(message):
        msg = str(message or "").strip()
        with ns["backup_warning_lock"]:
            ns["backup_warning_seq"] += 1
            ns["backup_warning_message"] = msg
            ns["backup_warning_at"] = time.time()

    def get_backup_warning_state(ttl_seconds=None):
        ttl = ns["BACKUP_WARNING_TTL_SECONDS"] if ttl_seconds is None else float(ttl_seconds)
        with ns["backup_warning_lock"]:
            seq = int(ns["backup_warning_seq"])
            msg = str(ns["backup_warning_message"] or "")
            at = float(ns["backup_warning_at"] or 0.0)
        if not msg:
            return {"seq": seq, "message": ""}
        if ttl > 0 and (time.time() - at) > ttl:
            return {"seq": seq, "message": ""}
        return {"seq": seq, "message": msg}

    return {
        "_mark_file_page_client_active": _mark_file_page_client_active,
        "get_cached_file_page_items": get_cached_file_page_items,
        "file_page_cache_refresher_loop": file_page_cache_refresher_loop,
        "ensure_file_page_cache_refresher_started": ensure_file_page_cache_refresher_started,
        "warm_file_page_caches": warm_file_page_caches,
        "set_service_status_intent": set_service_status_intent,
        "get_service_status_intent": get_service_status_intent,
        "stop_service_systemd": stop_service_systemd,
        "run_sudo": run_sudo,
        "validate_sudo_password": validate_sudo_password,
        "ensure_session_file": ensure_session_file,
        "read_session_start_time": read_session_start_time,
        "write_session_start_time": write_session_start_time,
        "clear_session_start_time": clear_session_start_time,
        "get_session_start_time": get_session_start_time,
        "get_session_duration_text": get_session_duration_text,
        "_log_source_settings": _log_source_settings,
        "get_log_source_text": get_log_source_text,
        "ensure_log_stream_fetcher_started": ensure_log_stream_fetcher_started,
        "_increment_log_stream_clients": _increment_log_stream_clients,
        "_decrement_log_stream_clients": _decrement_log_stream_clients,
        "get_backups_status": get_backups_status,
        "get_cpu_per_core_items": get_cpu_per_core_items,
        "get_ram_usage_class": get_ram_usage_class,
        "get_storage_usage_class": get_storage_usage_class,
        "get_storage_used_percent": get_storage_used_percent,
        "get_storage_available_percent": get_storage_available_percent,
        "is_storage_low": is_storage_low,
        "low_storage_error_message": low_storage_error_message,
        "get_cpu_frequency_class": get_cpu_frequency_class,
        "_refresh_rcon_config": _refresh_rcon_config,
        "is_rcon_enabled": is_rcon_enabled,
        "is_rcon_startup_ready": is_rcon_startup_ready,
        "_run_mcrcon": _run_mcrcon,
        "_probe_minecraft_runtime_metrics": _probe_minecraft_runtime_metrics,
        "get_players_online": get_players_online,
        "get_tick_rate": get_tick_rate,
        "get_service_status_display": get_service_status_display,
        "get_service_status_class": get_service_status_class,
        "graceful_stop_minecraft": graceful_stop_minecraft,
        "stop_server_automatically": stop_server_automatically,
        "ensure_startup_rcon_settings": ensure_startup_rcon_settings,
        "start_service_non_blocking": start_service_non_blocking,
        "run_backup_script": run_backup_script,
        "restore_world_backup": restore_world_backup,
        "append_restore_event": append_restore_event,
        "start_restore_job": start_restore_job,
        "get_restore_status": get_restore_status,
        "format_backup_time": format_backup_time,
        "get_server_time_text": get_server_time_text,
        "get_latest_backup_zip_timestamp": get_latest_backup_zip_timestamp,
        "get_backup_zip_snapshot": get_backup_zip_snapshot,
        "backup_snapshot_changed": backup_snapshot_changed,
        "get_backup_schedule_times": get_backup_schedule_times,
        "get_backup_status": get_backup_status,
        "is_backup_running": is_backup_running,
        "set_backup_warning": set_backup_warning,
        "get_backup_warning_state": get_backup_warning_state,
        "reset_backup_schedule_state": reset_backup_schedule_state,
        "collect_dashboard_metrics": collect_dashboard_metrics,
        "get_observed_state": get_observed_state,
        "invalidate_observed_state_cache": invalidate_observed_state_cache,
        "get_consistency_report": get_consistency_report,
        "_mark_home_page_client_active": _mark_home_page_client_active,
        "_collect_and_publish_metrics": _collect_and_publish_metrics,
        "metrics_collector_loop": metrics_collector_loop,
        "ensure_metrics_collector_started": ensure_metrics_collector_started,
        "start_operation_reconciler": start_operation_reconciler,
        "get_cached_dashboard_metrics": get_cached_dashboard_metrics,
        "format_countdown": format_countdown,
        "get_idle_countdown": get_idle_countdown,
        "idle_player_watcher": idle_player_watcher,
        "start_idle_player_watcher": start_idle_player_watcher,
        "backup_session_watcher": backup_session_watcher,
        "start_backup_session_watcher": start_backup_session_watcher,
        "storage_safety_watcher": storage_safety_watcher,
        "start_storage_safety_watcher": start_storage_safety_watcher,
        "initialize_session_tracking": initialize_session_tracking,
        "_status_state_note": _status_state_note,
    }
