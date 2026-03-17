"""Build the runtime context, bind services, and register routes."""

from dataclasses import dataclass
from typing import Any, FrozenSet, Mapping

from app.core.device_map import get_device_name_map as _default_device_name_map_lookup
from app.services import bootstrap as _default_bootstrap_service


@dataclass(frozen=True)
class RuntimeWiringConfig:
    """Configuration bundle for runtime wiring."""

    required_state_key_set: FrozenSet[str]
    runtime_context_extra_keys: FrozenSet[str]
    runtime_imported_symbols: Mapping[str, Any]


@dataclass(frozen=True)
class RuntimeServices:
    """Service dependency bundle for runtime wiring."""

    world_bindings_service: Any
    system_bindings_service: Any
    runtime_bindings_service: Any
    request_bindings_service: Any
    state_builder_service: Any
    app_lifecycle_service: Any
    session_store_service: Any
    minecraft_runtime_service: Any
    session_watchers_service: Any
    control_plane_service: Any
    dashboard_file_runtime_service: Any
    dashboard_log_runtime_service: Any
    dashboard_state_runtime_service: Any
    dashboard_metrics_runtime_service: Any
    dashboard_operations_runtime_service: Any
    status_cache_service: Any


def _build_runtime_context(namespace, required_state_key_set, runtime_context_extra_keys, runtime_imported_symbols):
    """Assemble the initial runtime context from explicit state keys and imports."""
    allowed = required_state_key_set | runtime_context_extra_keys
    runtime_context = {key: namespace[key] for key in allowed if key in namespace}
    runtime_context.update(runtime_imported_symbols)
    runtime_context.setdefault("STATE", None)
    return runtime_context


def _install_binding_stage(stage_name, mapping, binding_stage_exports, binding_stage_values):
    """Merge a binding stage while rejecting duplicate exported keys."""
    duplicates = sorted(set(mapping.keys()) & binding_stage_exports)
    if duplicates:
        raise KeyError(
            f"Duplicate binding keys in stage '{stage_name}': {', '.join(duplicates)}"
        )
    binding_stage_exports.update(mapping.keys())
    binding_stage_values.update(mapping)


def _install_lifecycle_hooks(app_lifecycle_service, app, binding, namespace):
    """Install Flask lifecycle hooks from the resolved runtime bindings."""
    process_role = str(namespace.get("PROCESS_ROLE", "all") or "all").strip().lower()
    app_lifecycle_service.install_flask_hooks(
        app,
        ensure_session_tracking_initialized=binding("ensure_session_tracking_initialized"),
        ensure_metrics_collector_started=binding("ensure_metrics_collector_started"),
        enable_metrics_collector_autostart=True,
        start_operation_reconciler=binding("start_operation_reconciler"),
        start_idle_player_watcher=binding("start_idle_player_watcher"),
        start_backup_session_watcher=binding("start_backup_session_watcher"),
        start_storage_safety_watcher=binding("start_storage_safety_watcher"),
        enable_background_watchers_autostart=process_role == "web",
        ensure_csrf_token=binding("_ensure_csrf_token"),
        is_csrf_valid=binding("_is_csrf_valid"),
        csrf_rejected_response=binding("_csrf_rejected_response"),
        log_mcweb_action=namespace["log_mcweb_action"],
        log_mcweb_exception=namespace["log_mcweb_exception"],
    )


def _build_run_server(app_lifecycle_service, app, namespace, binding):
    """Create the app startup entrypoint from the resolved runtime bindings."""
    bootstrap_service = namespace.get("bootstrap_service") or _default_bootstrap_service
    process_role = str(namespace.get("PROCESS_ROLE", "all") or "all").strip().lower()
    return app_lifecycle_service.build_run_server(
        bootstrap_service=bootstrap_service,
        app=app,
        app_config=namespace["APP_CONFIG"],
        log_mcweb_log=namespace["log_mcweb_log"],
        log_mcweb_exception=namespace["log_mcweb_exception"],
        is_backup_running=binding("is_backup_running"),
        load_backup_log_cache_from_disk=binding("_load_backup_log_cache_from_disk"),
        load_minecraft_log_cache_from_journal=binding("_load_minecraft_log_cache_from_journal"),
        load_mcweb_log_cache_from_disk=binding("_load_mcweb_log_cache_from_disk"),
        ensure_log_stream_fetcher_started=binding("ensure_log_stream_fetcher_started"),
        ensure_session_tracking_initialized=binding("ensure_session_tracking_initialized"),
        warm_file_page_caches=binding("warm_file_page_caches"),
        ensure_metrics_collector_started=binding("ensure_metrics_collector_started"),
        collect_and_publish_metrics=binding("_collect_and_publish_metrics"),
        start_operation_reconciler=binding("start_operation_reconciler"),
        start_idle_player_watcher=binding("start_idle_player_watcher"),
        start_backup_session_watcher=binding("start_backup_session_watcher"),
        start_storage_safety_watcher=binding("start_storage_safety_watcher"),
        enable_background_workers=process_role != "web",
        enable_boot_runtime_tasks=process_role != "web",
    )


def create_runtime(
    *,
    app,
    namespace,
    wiring_config: RuntimeWiringConfig,
    services: RuntimeServices,
    register_routes,
):
    """Build the runtime context, register routes, and return the run-server entrypoint."""
    required_state_key_set = wiring_config.required_state_key_set
    runtime_context = _build_runtime_context(
        namespace,
        required_state_key_set,
        wiring_config.runtime_context_extra_keys,
        wiring_config.runtime_imported_symbols,
    )

    world_bindings = services.world_bindings_service.build_world_bindings(runtime_context)
    world_bindings["_refresh_world_dir_from_server_properties"]()

    device_name_map_lookup = namespace.get("_device_name_map_lookup") or _default_device_name_map_lookup
    stages = (
        (
            "world_bindings",
            world_bindings,
        ),
        (
            "system_bindings",
            services.system_bindings_service.build_system_bindings(
                runtime_context,
                status_cache_service=services.status_cache_service,
                dashboard_log_runtime_service=services.dashboard_log_runtime_service,
                device_name_map_lookup=device_name_map_lookup,
            ),
        ),
        (
            "runtime_bindings",
            services.runtime_bindings_service.build_runtime_bindings(
                runtime_context,
                dashboard_file_runtime_service=services.dashboard_file_runtime_service,
                dashboard_state_runtime_service=services.dashboard_state_runtime_service,
                dashboard_metrics_runtime_service=services.dashboard_metrics_runtime_service,
                dashboard_operations_runtime_service=services.dashboard_operations_runtime_service,
                control_plane_service=services.control_plane_service,
                session_store_service=services.session_store_service,
                minecraft_runtime_service=services.minecraft_runtime_service,
                session_watchers_service=services.session_watchers_service,
            ),
        ),
    )

    binding_stage_exports = set()
    binding_stage_values = {}
    for stage_name, mapping in stages:
        _install_binding_stage(stage_name, mapping, binding_stage_exports, binding_stage_values)

    request_bindings = services.request_bindings_service.build_request_bindings(
        session_store_service=services.session_store_service,
        session_state=namespace["session_state"],
        initialize_session_tracking=binding_stage_values["initialize_session_tracking"],
        status_state_note=binding_stage_values["_status_state_note"],
        low_storage_error_message=binding_stage_values["low_storage_error_message"],
        display_tz=namespace["DISPLAY_TZ"],
        get_device_name_map=binding_stage_values["get_device_name_map"],
        app_state_db_path=namespace["APP_STATE_DB_PATH"],
    )
    _install_binding_stage("request_bindings", request_bindings, binding_stage_exports, binding_stage_values)

    def binding(key):
        if key not in binding_stage_values:
            raise KeyError(f"Missing staged binding key: {key}")
        return binding_stage_values[key]

    runtime_context.update(binding_stage_values)
    _install_lifecycle_hooks(services.app_lifecycle_service, app, binding, namespace)

    services.state_builder_service.assert_required_keys_present(runtime_context)
    state = services.state_builder_service.build_app_state(runtime_context)
    runtime_context["STATE"] = state
    register_routes(app, state)

    return {
        "runtime_context": runtime_context,
        "state": state,
        "static_asset_version_fn": world_bindings["_static_asset_version"],
        "run_server": _build_run_server(services.app_lifecycle_service, app, namespace, binding),
    }
