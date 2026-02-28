"""Build DebugTools and expose its callable bindings for main.py."""
def build_disabled_debug_bindings(*, log_mcweb_log):
    """Return lightweight no-op debug bindings for non-debug profiles."""
    def _noop_prepare_debug_server_properties_bootup():
        return None

    def _noop_get_debug_server_properties_rows():
        return {"ok": False, "message": "Debug profile is disabled."}

    def _noop_set_debug_server_properties_values(_values):
        return {"ok": False, "message": "Debug profile is disabled."}

    def _noop_debug_explorer_list(_root_key, _rel_path=""):
        return {"ok": False, "message": "Debug profile is disabled."}

    def _noop_get_debug_env_rows():
        return []

    def _noop_apply_debug_env_overrides(_values):
        return ["Debug profile is disabled."]

    def _noop_debug_start_service():
        return False

    def _noop_debug_stop_service(_sudo_password):
        return False, "Debug profile is disabled."

    def _noop_debug_run_backup(trigger="manual"):
        return False

    def _noop_debug_schedule_backup(minutes, trigger="manual"):
        return False, "Debug profile is disabled."

    def _noop_reset_all_debug_overrides():
        return None

    def _noop_log_mcweb_boot_diagnostics():
        log_mcweb_log("boot", command="debug profile disabled")

    return {
        "debug_tools": None,
        "prepare_debug_server_properties_bootup": _noop_prepare_debug_server_properties_bootup,
        "get_debug_server_properties_rows": _noop_get_debug_server_properties_rows,
        "set_debug_server_properties_values": _noop_set_debug_server_properties_values,
        "debug_explorer_list": _noop_debug_explorer_list,
        "log_mcweb_boot_diagnostics": _noop_log_mcweb_boot_diagnostics,
        "reset_all_debug_overrides": _noop_reset_all_debug_overrides,
        "apply_debug_env_overrides": _noop_apply_debug_env_overrides,
        "get_debug_env_rows": _noop_get_debug_env_rows,
        "debug_start_service": _noop_debug_start_service,
        "debug_stop_service": _noop_debug_stop_service,
        "debug_run_backup": _noop_debug_run_backup,
        "debug_schedule_backup": _noop_debug_schedule_backup,
    }


def build_debug_bindings(
    *,
    debug_tools_service,
    debug_enabled,
    debug_world_name,
    debug_motd,
    data_dir,
    app_dir,
    service,
    backup_script,
    backup_log_file,
    mcweb_action_log_file,
    backup_state_file,
    session_file,
    server_properties_candidates,
    debug_server_properties_keys,
    debug_server_properties_forced_values,
    debug_server_properties_int_keys,
    debug_server_properties_bool_keys,
    debug_server_properties_enums,
    debug_env_lock,
    debug_env_original_values,
    debug_env_overrides,
    backup_state,
    app,
    namespace,
    log_mcweb_log,
    log_mcweb_exception,
    log_debug_page_action,
    refresh_world_dir,
    refresh_rcon_config,
    invalidate_status_cache,
    set_service_status_intent,
    write_session_start_time,
    validate_sudo_password,
    record_successful_password_ip,
    graceful_stop_minecraft,
    clear_session_start_time,
    reset_backup_schedule_state,
    run_backup_script,
):
        # Instantiate DebugTools from explicit dependencies and export method aliases.
    debug_tools = debug_tools_service.DebugTools(
        debug_enabled=debug_enabled,
        debug_world_name=debug_world_name,
        debug_motd=debug_motd,
        data_dir=data_dir,
        app_dir=app_dir,
        service=service,
        backup_script=backup_script,
        backup_log_file=backup_log_file,
        mcweb_action_log_file=mcweb_action_log_file,
        backup_state_file=backup_state_file,
        session_file=session_file,
        server_properties_candidates=server_properties_candidates,
        debug_server_properties_keys=debug_server_properties_keys,
        debug_server_properties_forced_values=debug_server_properties_forced_values,
        debug_server_properties_int_keys=debug_server_properties_int_keys,
        debug_server_properties_bool_keys=debug_server_properties_bool_keys,
        debug_server_properties_enums=debug_server_properties_enums,
        debug_env_lock=debug_env_lock,
        debug_env_original_values=debug_env_original_values,
        debug_env_overrides=debug_env_overrides,
        backup_state=backup_state,
        app=app,
        namespace=namespace,
        log_mcweb_log=log_mcweb_log,
        log_mcweb_exception=log_mcweb_exception,
        log_debug_page_action=log_debug_page_action,
        refresh_world_dir=refresh_world_dir,
        refresh_rcon_config=refresh_rcon_config,
        invalidate_status_cache=invalidate_status_cache,
        set_service_status_intent=set_service_status_intent,
        write_session_start_time=write_session_start_time,
        validate_sudo_password=validate_sudo_password,
        record_successful_password_ip=record_successful_password_ip,
        graceful_stop_minecraft=graceful_stop_minecraft,
        clear_session_start_time=clear_session_start_time,
        reset_backup_schedule_state=reset_backup_schedule_state,
        run_backup_script=run_backup_script,
    )
    return {
        "debug_tools": debug_tools,
        "prepare_debug_server_properties_bootup": debug_tools.prepare_debug_server_properties_bootup,
        "get_debug_server_properties_rows": debug_tools.get_debug_server_properties_rows,
        "set_debug_server_properties_values": debug_tools.set_debug_server_properties_values,
        "debug_explorer_list": debug_tools.debug_explorer_list,
        "log_mcweb_boot_diagnostics": debug_tools.log_mcweb_boot_diagnostics,
        "reset_all_debug_overrides": debug_tools.reset_all_debug_overrides,
        "apply_debug_env_overrides": debug_tools.apply_debug_env_overrides,
        "get_debug_env_rows": debug_tools.get_debug_env_rows,
        "debug_start_service": debug_tools.debug_start_service,
        "debug_stop_service": debug_tools.debug_stop_service,
        "debug_run_backup": debug_tools.debug_run_backup,
        "debug_schedule_backup": debug_tools.debug_schedule_backup,
    }
