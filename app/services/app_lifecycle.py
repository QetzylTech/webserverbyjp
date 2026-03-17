"""Install Flask hooks and build the app startup runner."""

from flask import has_request_context, request
from werkzeug.exceptions import HTTPException

from app.core.response_helpers import internal_error_response


def install_flask_hooks(
    app,
    *,
    ensure_session_tracking_initialized,
    ensure_metrics_collector_started,
    enable_metrics_collector_autostart=True,
    start_operation_reconciler=None,
    start_idle_player_watcher=None,
    start_backup_session_watcher=None,
    start_storage_safety_watcher=None,
    enable_background_watchers_autostart=False,
    ensure_csrf_token,
    is_csrf_valid,
    csrf_rejected_response,
    log_mcweb_action,
    log_mcweb_exception,
):
    """Install request and error hooks from explicit runtime callbacks."""

    @app.before_request
    def _initialize_session_tracking_before_request():
        ensure_session_tracking_initialized()
        if enable_metrics_collector_autostart:
            ensure_metrics_collector_started()
        if enable_background_watchers_autostart:
            if callable(start_operation_reconciler):
                start_operation_reconciler()
            if callable(start_idle_player_watcher):
                start_idle_player_watcher()
            if callable(start_backup_session_watcher):
                start_backup_session_watcher()
            if callable(start_storage_safety_watcher):
                start_storage_safety_watcher()
        ensure_csrf_token()
        csrf_exempt_paths = {"/home-heartbeat", "/file-page-heartbeat", "/setup", "/setup/submit", "/setup/validate"}
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
        if isinstance(exc, HTTPException):
            return exc
        path = request.path if has_request_context() else "unknown-path"
        log_mcweb_exception(f"unhandled_exception path={path}", exc)
        return internal_error_response(request)


def build_run_server(
    *,
    bootstrap_service,
    app,
    app_config,
    log_mcweb_log,
    log_mcweb_exception,
    is_backup_running,
    load_backup_log_cache_from_disk,
    load_minecraft_log_cache_from_journal,
    load_mcweb_log_cache_from_disk,
    ensure_log_stream_fetcher_started=None,
    log_stream_autostart_sources=None,
    ensure_session_tracking_initialized,
    warm_file_page_caches,
    ensure_metrics_collector_started,
    collect_and_publish_metrics,
    start_operation_reconciler,
    start_idle_player_watcher,
    start_backup_session_watcher,
    start_storage_safety_watcher,
    enable_background_workers=True,
    enable_boot_runtime_tasks=True,
):
    """Return the startup runner assembled from explicit boot steps."""

    def run_server():
        def _load_backup_log_cache_boot_step():
            if not is_backup_running():
                load_backup_log_cache_from_disk()

        def _start_log_stream_fetchers():
            if not callable(ensure_log_stream_fetcher_started):
                return
            sources = log_stream_autostart_sources
            if sources is None:
                sources = ("minecraft", "backup")
            for source in sources:
                try:
                    ensure_log_stream_fetcher_started(source)
                except Exception as exc:
                    log_mcweb_exception(f"log_stream_autostart/{source}", exc)

        def _build_boot_steps():
            steps = [
                ("load_minecraft_log_cache_from_journal", load_minecraft_log_cache_from_journal),
                ("load_mcweb_log_cache_from_disk", load_mcweb_log_cache_from_disk),
                ("load_backup_log_cache_from_disk", _load_backup_log_cache_boot_step),
                ("ensure_session_tracking_initialized", ensure_session_tracking_initialized),
                ("warm_file_page_caches", warm_file_page_caches),
                ("ensure_metrics_collector_started", ensure_metrics_collector_started),
                ("collect_and_publish_metrics", collect_and_publish_metrics),
            ]
            if enable_background_workers:
                steps.append(("start_log_stream_fetchers", _start_log_stream_fetchers))
                steps.extend(
                    [
                        ("start_operation_reconciler", start_operation_reconciler),
                        ("start_idle_player_watcher", start_idle_player_watcher),
                        ("start_backup_session_watcher", start_backup_session_watcher),
                        ("start_storage_safety_watcher", start_storage_safety_watcher),
                    ]
                )
            return steps

        if not enable_boot_runtime_tasks:
            bootstrap_service.run_server(
                app,
                app_config,
                log_mcweb_log,
                log_mcweb_exception,
                boot_steps=[],
            )
            return

        bootstrap_service.run_server(
            app,
            app_config,
            log_mcweb_log,
            log_mcweb_exception,
            _build_boot_steps(),
        )

    return run_server
