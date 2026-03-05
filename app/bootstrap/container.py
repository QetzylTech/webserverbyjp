"""Runtime container/composition helpers."""

from __future__ import annotations


def build_runtime_bundle(
    *,
    runtime_wiring_service,
    app,
    namespace,
    required_state_key_set,
    runtime_context_extra_keys,
    runtime_imported_symbols,
    world_bindings_service,
    system_bindings_service,
    runtime_bindings_service,
    request_bindings_service,
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
    """Build the app runtime bundle via runtime wiring."""
    return runtime_wiring_service.create_runtime(
        app=app,
        namespace=namespace,
        required_state_key_set=required_state_key_set,
        runtime_context_extra_keys=runtime_context_extra_keys,
        runtime_imported_symbols=runtime_imported_symbols,
        world_bindings_service=world_bindings_service,
        system_bindings_service=system_bindings_service,
        runtime_bindings_service=runtime_bindings_service,
        request_bindings_service=request_bindings_service,
        state_builder_service=state_builder_service,
        app_lifecycle_service=app_lifecycle_service,
        session_store_service=session_store_service,
        minecraft_runtime_service=minecraft_runtime_service,
        session_watchers_service=session_watchers_service,
        control_plane_service=control_plane_service,
        dashboard_runtime_service=dashboard_runtime_service,
        status_cache_service=status_cache_service,
        register_routes=register_routes,
    )
