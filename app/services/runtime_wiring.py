"""Runtime wiring helpers extracted from app.main."""


def create_runtime(
    *,
    app,
    namespace,
    required_state_key_set,
    runtime_context_extra_keys,
    runtime_imported_symbols,
    world_bindings_service,
    system_bindings_service,
    runtime_bindings_service,
    request_bindings_service,
    debug_bindings_service,
    debug_tools_service,
    state_builder_service,
    app_lifecycle_service,
    session_store_service,
    minecraft_runtime_service,
    session_watchers_service,
    control_plane_service,
    dashboard_runtime_service,
    status_cache_service,
    register_routes,
):
    """Build runtime context/bindings, register routes, and return run-server entrypoint."""
    allowed = required_state_key_set | runtime_context_extra_keys
    runtime_context = {key: namespace[key] for key in allowed if key in namespace}
    runtime_context.update(runtime_imported_symbols)
    runtime_context.setdefault("STATE", None)

    world_bindings = world_bindings_service.build_world_bindings(runtime_context)
    binding_stage_exports = set()
    binding_stage_values = {}

    def install_binding_stage(stage_name, mapping):
        duplicates = sorted(set(mapping.keys()) & binding_stage_exports)
        if duplicates:
            raise KeyError(
                f"Duplicate binding keys in stage '{stage_name}': {', '.join(duplicates)}"
            )
        binding_stage_exports.update(mapping.keys())
        binding_stage_values.update(mapping)

    install_binding_stage("world_bindings", world_bindings)
    world_bindings["_refresh_world_dir_from_server_properties"]()

    system_bindings = system_bindings_service.build_system_bindings(
        runtime_context,
        status_cache_service=status_cache_service,
        dashboard_runtime_service=dashboard_runtime_service,
        device_name_map_lookup=namespace["_device_name_map_lookup"],
    )
    install_binding_stage("system_bindings", system_bindings)

    runtime_bindings = runtime_bindings_service.build_runtime_bindings(
        runtime_context,
        dashboard_runtime_service=dashboard_runtime_service,
        control_plane_service=control_plane_service,
        session_store_service=session_store_service,
        minecraft_runtime_service=minecraft_runtime_service,
        session_watchers_service=session_watchers_service,
    )
    install_binding_stage("runtime_bindings", runtime_bindings)

    request_bindings = request_bindings_service.build_request_bindings(
        session_store_service=session_store_service,
        session_state=namespace["session_state"],
        initialize_session_tracking=runtime_bindings["initialize_session_tracking"],
        status_debug_note=runtime_bindings["_status_debug_note"],
        low_storage_error_message=runtime_bindings["low_storage_error_message"],
        display_tz=namespace["DISPLAY_TZ"],
        get_device_name_map=system_bindings["get_device_name_map"],
        app_state_db_path=namespace["APP_STATE_DB_PATH"],
    )
    install_binding_stage("request_bindings", request_bindings)

    debug_bindings = debug_bindings_service.build_debug_bindings(
        debug_tools_service=debug_tools_service,
        debug_enabled=namespace["DEBUG_ENABLED"],
        debug_world_name=namespace["DEBUG_WORLD_NAME"],
        debug_motd=namespace["DEBUG_MOTD"],
        data_dir=namespace["DATA_DIR"],
        app_dir=namespace["APP_DIR"],
        service=namespace["SERVICE"],
        backup_script=namespace["BACKUP_SCRIPT"],
        backup_log_file=namespace["BACKUP_LOG_FILE"],
        mcweb_action_log_file=namespace["MCWEB_ACTION_LOG_FILE"],
        backup_state_file=namespace["BACKUP_STATE_FILE"],
        session_file=namespace["SESSION_FILE"],
        server_properties_candidates=namespace["SERVER_PROPERTIES_CANDIDATES"],
        debug_server_properties_keys=namespace["DEBUG_SERVER_PROPERTIES_KEYS"],
        debug_server_properties_forced_values=namespace["DEBUG_SERVER_PROPERTIES_FORCED_VALUES"],
        debug_server_properties_int_keys=namespace["DEBUG_SERVER_PROPERTIES_INT_KEYS"],
        debug_server_properties_bool_keys=namespace["DEBUG_SERVER_PROPERTIES_BOOL_KEYS"],
        debug_server_properties_enums=namespace["DEBUG_SERVER_PROPERTIES_ENUMS"],
        debug_env_lock=namespace["debug_env_lock"],
        debug_env_original_values=namespace["debug_env_original_values"],
        debug_env_overrides=namespace["debug_env_overrides"],
        backup_state=namespace["backup_state"],
        app=app,
        namespace=runtime_context,
        log_mcweb_log=namespace["log_mcweb_log"],
        log_mcweb_exception=namespace["log_mcweb_exception"],
        log_debug_page_action=namespace["log_debug_page_action"],
        refresh_world_dir=world_bindings["_refresh_world_dir_from_server_properties"],
        refresh_rcon_config=runtime_bindings["_refresh_rcon_config"],
        invalidate_status_cache=system_bindings["invalidate_status_cache"],
        set_service_status_intent=runtime_bindings["set_service_status_intent"],
        write_session_start_time=runtime_bindings["write_session_start_time"],
        validate_sudo_password=runtime_bindings["validate_sudo_password"],
        record_successful_password_ip=request_bindings["record_successful_password_ip"],
        graceful_stop_minecraft=runtime_bindings["graceful_stop_minecraft"],
        clear_session_start_time=runtime_bindings["clear_session_start_time"],
        reset_backup_schedule_state=runtime_bindings["reset_backup_schedule_state"],
        run_backup_script=runtime_bindings["run_backup_script"],
    )
    install_binding_stage("debug_bindings", debug_bindings)

    def binding(key):
        if key not in binding_stage_values:
            raise KeyError(f"Missing staged binding key: {key}")
        return binding_stage_values[key]

    runtime_context.update(binding_stage_values)
    app_lifecycle_service.install_flask_hooks(
        app,
        ensure_session_tracking_initialized=binding("ensure_session_tracking_initialized"),
        ensure_metrics_collector_started=binding("ensure_metrics_collector_started"),
        ensure_csrf_token=binding("_ensure_csrf_token"),
        is_csrf_valid=binding("_is_csrf_valid"),
        csrf_rejected_response=binding("_csrf_rejected_response"),
        log_mcweb_action=namespace["log_mcweb_action"],
        log_mcweb_exception=namespace["log_mcweb_exception"],
    )

    state_builder_service.assert_required_keys_present(runtime_context)
    state = state_builder_service.build_app_state(runtime_context)
    runtime_context["STATE"] = state
    register_routes(app, state)

    run_server = app_lifecycle_service.build_run_server(
        bootstrap_service=namespace["bootstrap_service"],
        app=app,
        cfg_get_str=namespace["_cfg_str"],
        cfg_get_int=namespace["_cfg_int"],
        log_mcweb_log=namespace["log_mcweb_log"],
        log_mcweb_exception=namespace["log_mcweb_exception"],
        is_backup_running=binding("is_backup_running"),
        load_backup_log_cache_from_disk=binding("_load_backup_log_cache_from_disk"),
        prepare_debug_server_properties_bootup=binding("prepare_debug_server_properties_bootup"),
        log_mcweb_boot_diagnostics=binding("log_mcweb_boot_diagnostics"),
        load_minecraft_log_cache_from_journal=binding("_load_minecraft_log_cache_from_journal"),
        load_mcweb_log_cache_from_disk=binding("_load_mcweb_log_cache_from_disk"),
        ensure_session_tracking_initialized=binding("ensure_session_tracking_initialized"),
        ensure_metrics_collector_started=binding("ensure_metrics_collector_started"),
        collect_and_publish_metrics=binding("_collect_and_publish_metrics"),
        start_idle_player_watcher=binding("start_idle_player_watcher"),
        start_backup_session_watcher=binding("start_backup_session_watcher"),
        start_storage_safety_watcher=binding("start_storage_safety_watcher"),
    )

    return {
        "runtime_context": runtime_context,
        "state": state,
        "static_asset_version_fn": world_bindings["_static_asset_version"],
        "run_server": run_server,
    }
