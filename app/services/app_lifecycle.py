"""Flask lifecycle hook and startup runner composition helpers."""
from flask import has_request_context, request
from app.core.response_helpers import internal_error_response


def install_flask_hooks(
    app,
    *,
    ensure_session_tracking_initialized,
    ensure_metrics_collector_started,
    ensure_csrf_token,
    is_csrf_valid,
    csrf_rejected_response,
    log_mcweb_action,
    log_mcweb_exception,
):
        # Install request/error hooks using explicit runtime callbacks.

    @app.before_request
    def _initialize_session_tracking_before_request():
        ensure_session_tracking_initialized()
        ensure_metrics_collector_started()
        ensure_csrf_token()
        csrf_exempt_paths = {"/home-heartbeat", "/file-page-heartbeat"}
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.path not in csrf_exempt_paths
            and not is_csrf_valid()
        ):
            log_mcweb_action(
                "reject",
                command=request.path,
                rejection_message="Security check failed (csrf_invalid).",
            )
            return csrf_rejected_response()

    @app.errorhandler(Exception)
    def _unhandled_exception_handler(exc):
        path = request.path if has_request_context() else "unknown-path"
        log_mcweb_exception(f"unhandled_exception path={path}", exc)
        return internal_error_response(request)


def build_run_server(
    *,
    bootstrap_service,
    app,
    cfg_get_str,
    cfg_get_int,
    log_mcweb_log,
    log_mcweb_exception,
    is_backup_running,
    load_backup_log_cache_from_disk,
    prepare_debug_server_properties_bootup,
    log_mcweb_boot_diagnostics,
    load_minecraft_log_cache_from_journal,
    load_mcweb_log_cache_from_disk,
    ensure_session_tracking_initialized,
    ensure_metrics_collector_started,
    collect_and_publish_metrics,
    start_idle_player_watcher,
    start_backup_session_watcher,
    start_storage_safety_watcher,
):
        # Return the app startup runner from explicit boot-step dependencies.

    def run_server():
        def _load_backup_log_cache_boot_step():
            if not is_backup_running():
                load_backup_log_cache_from_disk()

        boot_steps = [
            ("prepare_debug_server_properties_bootup", prepare_debug_server_properties_bootup),
            ("log_mcweb_boot_diagnostics", log_mcweb_boot_diagnostics),
            ("load_minecraft_log_cache_from_journal", load_minecraft_log_cache_from_journal),
            ("load_mcweb_log_cache_from_disk", load_mcweb_log_cache_from_disk),
            ("load_backup_log_cache_from_disk", _load_backup_log_cache_boot_step),
            ("ensure_session_tracking_initialized", ensure_session_tracking_initialized),
            ("ensure_metrics_collector_started", ensure_metrics_collector_started),
            ("collect_and_publish_metrics", collect_and_publish_metrics),
            ("start_idle_player_watcher", start_idle_player_watcher),
            ("start_backup_session_watcher", start_backup_session_watcher),
            ("start_storage_safety_watcher", start_storage_safety_watcher),
        ]

        bootstrap_service.run_server(
            app,
            cfg_get_str,
            cfg_get_int,
            log_mcweb_log,
            log_mcweb_exception,
            boot_steps,
        )

    return run_server
