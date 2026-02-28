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
        return ns["STATE"]

    def _mark_file_page_client_active():
        return dashboard_runtime_service.mark_file_page_client_active(_state())

    def get_cached_file_page_items(cache_key):
        return dashboard_runtime_service.get_cached_file_page_items(_state(), cache_key)

    def file_page_cache_refresher_loop():
        return dashboard_runtime_service.file_page_cache_refresher_loop(_state())

    def ensure_file_page_cache_refresher_started():
        return dashboard_runtime_service.ensure_file_page_cache_refresher_started(_state())

    def set_service_status_intent(intent):
        return control_plane_service.set_service_status_intent(_state(), intent)

    def get_service_status_intent():
        return control_plane_service.get_service_status_intent(_state())

    def stop_service_systemd():
        return control_plane_service.stop_service_systemd(_state())

    def get_sudo_password():
        return control_plane_service.get_sudo_password(_state())

    def run_sudo(cmd):
        return control_plane_service.run_sudo(_state(), cmd)

    def validate_sudo_password(sudo_password):
        return control_plane_service.validate_sudo_password(_state(), sudo_password)

    def ensure_session_file():
        return session_store_service.ensure_session_file(control_plane_service, _state())

    def read_session_start_time():
        return session_store_service.read_session_start_time(control_plane_service, _state())

    def write_session_start_time(timestamp=None):
        return session_store_service.write_session_start_time(control_plane_service, _state(), timestamp)

    def clear_session_start_time():
        return session_store_service.clear_session_start_time(control_plane_service, _state())

    def get_session_start_time(service_status=None):
        return session_store_service.get_session_start_time(control_plane_service, _state(), service_status)

    def get_session_duration_text():
        return session_store_service.get_session_duration_text(control_plane_service, _state())

    def _log_source_settings(source):
        return minecraft_runtime_service.log_source_settings(_state(), source)

    def get_log_source_text(source):
        return minecraft_runtime_service.get_log_source_text(_state(), source)

    def ensure_log_stream_fetcher_started(source):
        return minecraft_runtime_service.ensure_log_stream_fetcher_started(_state(), source)

    def _increment_log_stream_clients(source):
        return minecraft_runtime_service.increment_log_stream_clients(_state(), source)

    def _decrement_log_stream_clients(source):
        return minecraft_runtime_service.decrement_log_stream_clients(_state(), source)

    def get_backups_status():
        return dashboard_runtime_service.get_backups_status(_state())

    def get_cpu_per_core_items(cpu_per_core):
        return dashboard_runtime_service.get_cpu_per_core_items(_state(), cpu_per_core)

    def get_ram_usage_class(ram_usage):
        return dashboard_runtime_service.get_ram_usage_class(_state(), ram_usage)

    def get_storage_usage_class(storage_usage):
        return dashboard_runtime_service.get_storage_usage_class(_state(), storage_usage)

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

    def get_cpu_frequency_class(cpu_frequency):
        return dashboard_runtime_service.get_cpu_frequency_class(_state(), cpu_frequency)

    def _refresh_rcon_config():
        return minecraft_runtime_service.refresh_rcon_config(_state())

    def is_rcon_enabled():
        return minecraft_runtime_service.is_rcon_enabled(_state())

    def _run_mcrcon(command, timeout=4):
        return minecraft_runtime_service.run_mcrcon(_state(), command, timeout=timeout)

    def _probe_minecraft_runtime_metrics(force=False):
        return minecraft_runtime_service.probe_minecraft_runtime_metrics(_state(), force=force)

    def get_players_online():
        return minecraft_runtime_service.get_players_online(_state())

    def get_tick_rate():
        return minecraft_runtime_service.get_tick_rate(_state())

    def get_service_status_display(service_status, players_online):
        return minecraft_runtime_service.get_service_status_display(_state(), service_status, players_online)

    def get_service_status_class(service_status_display):
        return minecraft_runtime_service.get_service_status_class(service_status_display)

    def graceful_stop_minecraft(trigger="session_end"):
        return control_plane_service.graceful_stop_minecraft(_state(), trigger=trigger)

    def stop_server_automatically(trigger="session_end"):
        return control_plane_service.stop_server_automatically(_state(), trigger=trigger)

    def run_backup_script(count_skip_as_success=True, trigger="manual"):
        return control_plane_service.run_backup_script(_state(), count_skip_as_success, trigger)

    def restore_world_backup(backup_filename):
        return control_plane_service.restore_world_backup(_state(), backup_filename)

    def append_restore_event(message):
        return control_plane_service.append_restore_event(_state(), message)

    def start_restore_job(backup_filename):
        return control_plane_service.start_restore_job(_state(), backup_filename)

    def get_restore_status(since_seq=0, job_id=None):
        return control_plane_service.get_restore_status(_state(), since_seq=since_seq, job_id=job_id)

    def start_undo_restore_job():
        return control_plane_service.start_undo_restore_job(_state())

    def format_backup_time(timestamp):
        return control_plane_service.format_backup_time(_state(), timestamp)

    def get_server_time_text():
        return control_plane_service.get_server_time_text(_state())

    def get_latest_backup_zip_timestamp():
        return control_plane_service.get_latest_backup_zip_timestamp(_state())

    def get_backup_zip_snapshot():
        return control_plane_service.get_backup_zip_snapshot(_state())

    def backup_snapshot_changed(before_snapshot, after_snapshot):
        return control_plane_service.backup_snapshot_changed(_state(), before_snapshot, after_snapshot)

    def get_backup_schedule_times(service_status=None):
        return control_plane_service.get_backup_schedule_times(_state(), service_status)

    def get_backup_status():
        return control_plane_service.get_backup_status(_state())

    def is_backup_running():
        return control_plane_service.is_backup_running(_state())

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

    def reset_backup_schedule_state():
        return control_plane_service.reset_backup_schedule_state(_state())

    def collect_dashboard_metrics():
        return dashboard_runtime_service.collect_dashboard_metrics(_state())

    def _mark_home_page_client_active():
        return dashboard_runtime_service.mark_home_page_client_active(_state())

    def _collect_and_publish_metrics():
        return dashboard_runtime_service.collect_and_publish_metrics(_state())

    def metrics_collector_loop():
        return dashboard_runtime_service.metrics_collector_loop(_state())

    def ensure_metrics_collector_started():
        return dashboard_runtime_service.ensure_metrics_collector_started(_state())

    def get_cached_dashboard_metrics():
        return dashboard_runtime_service.get_cached_dashboard_metrics(_state())

    def format_countdown(seconds):
        return session_watchers_service.format_countdown(seconds)

    def get_idle_countdown(service_status=None, players_online=None):
        return session_watchers_service.get_idle_countdown(_state(), service_status, players_online)

    def idle_player_watcher():
        return session_watchers_service.idle_player_watcher(_state())

    def start_idle_player_watcher():
        return session_watchers_service.start_idle_player_watcher(_state())

    def backup_session_watcher():
        return session_watchers_service.backup_session_watcher(_state())

    def start_backup_session_watcher():
        return session_watchers_service.start_backup_session_watcher(_state())

    def storage_safety_watcher():
        return session_watchers_service.storage_safety_watcher(_state())

    def start_storage_safety_watcher():
        return session_watchers_service.start_storage_safety_watcher(_state())

    def initialize_session_tracking():
        return session_watchers_service.initialize_session_tracking(_state())

    def _status_debug_note():
        return session_watchers_service.status_debug_note(_state())

    return {
        "_mark_file_page_client_active": _mark_file_page_client_active,
        "get_cached_file_page_items": get_cached_file_page_items,
        "file_page_cache_refresher_loop": file_page_cache_refresher_loop,
        "ensure_file_page_cache_refresher_started": ensure_file_page_cache_refresher_started,
        "set_service_status_intent": set_service_status_intent,
        "get_service_status_intent": get_service_status_intent,
        "stop_service_systemd": stop_service_systemd,
        "get_sudo_password": get_sudo_password,
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
        "_run_mcrcon": _run_mcrcon,
        "_probe_minecraft_runtime_metrics": _probe_minecraft_runtime_metrics,
        "get_players_online": get_players_online,
        "get_tick_rate": get_tick_rate,
        "get_service_status_display": get_service_status_display,
        "get_service_status_class": get_service_status_class,
        "graceful_stop_minecraft": graceful_stop_minecraft,
        "stop_server_automatically": stop_server_automatically,
        "run_backup_script": run_backup_script,
        "restore_world_backup": restore_world_backup,
        "append_restore_event": append_restore_event,
        "start_restore_job": start_restore_job,
        "get_restore_status": get_restore_status,
        "start_undo_restore_job": start_undo_restore_job,
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
        "_mark_home_page_client_active": _mark_home_page_client_active,
        "_collect_and_publish_metrics": _collect_and_publish_metrics,
        "metrics_collector_loop": metrics_collector_loop,
        "ensure_metrics_collector_started": ensure_metrics_collector_started,
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
        "_status_debug_note": _status_debug_note,
    }
