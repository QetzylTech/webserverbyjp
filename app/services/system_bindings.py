"""Build status/cache/device-map helper callables for main.py."""
def build_system_bindings(namespace, *, status_cache_service, dashboard_runtime_service, device_name_map_lookup):
    """Return system helpers bound to runtime namespace."""
    ns = namespace

    def get_status():
        return status_cache_service.get_status(
            cache_lock=ns["service_status_cache_lock"],
            cache_value_ref=ns["service_status_cache_value_ref"],
            cache_at_ref=ns["service_status_cache_at_ref"],
            service=ns["SERVICE"],
            active_ttl_seconds=ns["SERVICE_STATUS_CACHE_ACTIVE_SECONDS"],
            off_ttl_seconds=ns["SERVICE_STATUS_CACHE_OFF_SECONDS"],
            timeout_seconds=ns["SERVICE_STATUS_COMMAND_TIMEOUT_SECONDS"],
            log_action=ns["log_mcweb_log"],
            log_exception=ns["log_mcweb_exception"],
        )

    def invalidate_status_cache():
        status_cache_service.invalidate_status_cache(
            ns["service_status_cache_lock"],
            ns["service_status_cache_value_ref"],
            ns["service_status_cache_at_ref"],
        )

    def _load_backup_log_cache_from_disk():
        return dashboard_runtime_service.load_backup_log_cache_from_disk(ns["STATE"])

    def _append_backup_log_cache_line(line):
        return dashboard_runtime_service.append_backup_log_cache_line(ns["STATE"], line)

    def _get_cached_backup_log_text():
        return dashboard_runtime_service.get_cached_backup_log_text(ns["STATE"])

    def _load_minecraft_log_cache_from_journal():
        return dashboard_runtime_service.load_minecraft_log_cache_from_journal(ns["STATE"])

    def _append_minecraft_log_cache_line(line):
        return dashboard_runtime_service.append_minecraft_log_cache_line(ns["STATE"], line)

    def _get_cached_minecraft_log_text():
        return dashboard_runtime_service.get_cached_minecraft_log_text(ns["STATE"])

    def _load_mcweb_log_cache_from_disk():
        return dashboard_runtime_service.load_mcweb_log_cache_from_disk(ns["STATE"])

    def _append_mcweb_log_cache_line(line):
        return dashboard_runtime_service.append_mcweb_log_cache_line(ns["STATE"], line)

    def _get_cached_mcweb_log_text():
        return dashboard_runtime_service.get_cached_mcweb_log_text(ns["STATE"])

    def get_device_name_map():
        return device_name_map_lookup(
            csv_path=ns["DEVICE_MAP_CSV_PATH"],
            fallback_path=ns["DEVICE_FALLMAP_PATH"],
            cache_lock=ns["device_name_map_lock"],
            cache=ns["device_name_map_cache"],
            cache_mtime_ns=ns["device_name_map_mtime_ns_ref"],
            log_exception=ns["log_mcweb_exception"],
        )

    return {
        "get_status": get_status,
        "invalidate_status_cache": invalidate_status_cache,
        "_load_backup_log_cache_from_disk": _load_backup_log_cache_from_disk,
        "_append_backup_log_cache_line": _append_backup_log_cache_line,
        "_get_cached_backup_log_text": _get_cached_backup_log_text,
        "_load_minecraft_log_cache_from_journal": _load_minecraft_log_cache_from_journal,
        "_append_minecraft_log_cache_line": _append_minecraft_log_cache_line,
        "_get_cached_minecraft_log_text": _get_cached_minecraft_log_text,
        "_load_mcweb_log_cache_from_disk": _load_mcweb_log_cache_from_disk,
        "_append_mcweb_log_cache_line": _append_mcweb_log_cache_line,
        "_get_cached_mcweb_log_text": _get_cached_mcweb_log_text,
        "get_device_name_map": get_device_name_map,
    }
