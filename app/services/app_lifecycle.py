"""Install Flask hooks and build the app startup runner."""

import threading
from typing import Any, Callable, Iterable

from flask import has_request_context, request
from werkzeug.exceptions import HTTPException

from app.core.response_helpers import internal_error_response


def install_flask_hooks(
    app: Any,
    *,
    ensure_session_tracking_initialized: Callable[[], object],
    ensure_csrf_token: Callable[[], object],
    is_csrf_valid: Callable[[], bool],
    csrf_rejected_response: Callable[[], Any],
    log_mcweb_action: Callable[..., object],
    log_mcweb_exception: Callable[..., object],
) -> None:
    """Install request and error hooks from explicit runtime callbacks."""

    def _initialize_session_tracking_before_request() -> Any:
        ensure_session_tracking_initialized()
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

    def _unhandled_exception_handler(exc: Exception) -> Any:
        if isinstance(exc, HTTPException):
            return exc
        path = request.path if has_request_context() else "unknown-path"
        log_mcweb_exception(f"unhandled_exception path={path}", exc)
        return internal_error_response(request)

    app.before_request(_initialize_session_tracking_before_request)
    app.register_error_handler(Exception, _unhandled_exception_handler)


def build_run_server(
    *,
    bootstrap_service: Any,
    app: Any,
    app_config: Any,
    log_mcweb_log: Callable[..., object],
    log_mcweb_exception: Callable[..., object],
    is_backup_running: Callable[[], bool],
    load_backup_log_cache_from_disk: Callable[[], object],
    load_minecraft_log_cache_from_journal: Callable[[], object],
    load_mcweb_log_cache_from_disk: Callable[[], object],
    ensure_session_tracking_initialized: Callable[[], object],
    warm_file_page_caches: Callable[[], object],
    collect_and_publish_metrics: Callable[[], object],
    start_worker_loops: Callable[[], object] | None = None,
    enable_background_workers: bool = True,
    enable_boot_runtime_tasks: bool = True,
    boot_runtime: Callable[[], object] | None = None,
) -> Callable[[], None]:
    """Return the startup runner assembled from explicit boot steps."""

    def _load_backup_log_cache_boot_step() -> None:
        if not is_backup_running():
            load_backup_log_cache_from_disk()

    def _build_boot_steps() -> list[tuple[str, Callable[[], object]]]:
        steps: list[tuple[str, Callable[[], object]]] = [
            ("load_minecraft_log_cache_from_journal", load_minecraft_log_cache_from_journal),
            ("load_mcweb_log_cache_from_disk", load_mcweb_log_cache_from_disk),
            ("load_backup_log_cache_from_disk", _load_backup_log_cache_boot_step),
            ("ensure_session_tracking_initialized", ensure_session_tracking_initialized),
            ("warm_file_page_caches", warm_file_page_caches),
            ("collect_and_publish_metrics", collect_and_publish_metrics),
        ]
        if enable_background_workers and callable(start_worker_loops):
            steps.append(("start_worker_loops", start_worker_loops))
        return steps

    def run_server() -> None:
        if callable(boot_runtime):
            boot_runtime()
            bootstrap_service.run_server(
                app,
                app_config,
                log_mcweb_log,
                log_mcweb_exception,
                boot_steps=[],
            )
            return

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


def build_boot_runtime(
    *,
    log_mcweb_log: Callable[..., object],
    log_mcweb_exception: Callable[..., object],
    is_backup_running: Callable[[], bool],
    load_backup_log_cache_from_disk: Callable[[], object],
    load_minecraft_log_cache_from_journal: Callable[[], object],
    load_mcweb_log_cache_from_disk: Callable[[], object],
    ensure_session_tracking_initialized: Callable[[], object],
    warm_file_page_caches: Callable[[], object],
    collect_and_publish_metrics: Callable[[], object],
    start_worker_loops: Callable[[], object] | None = None,
    enable_background_workers: bool = True,
    enable_boot_runtime_tasks: bool = True,
) -> Callable[[], None]:
    """Return an idempotent runtime bootstrap callable for WSGI/imported app paths."""

    boot_lock = threading.Lock()
    boot_completed = False

    def _load_backup_log_cache_boot_step() -> None:
        if not is_backup_running():
            load_backup_log_cache_from_disk()

    def _build_boot_steps() -> list[tuple[str, Callable[[], object]]]:
        steps: list[tuple[str, Callable[[], object]]] = [
            ("load_minecraft_log_cache_from_journal", load_minecraft_log_cache_from_journal),
            ("load_mcweb_log_cache_from_disk", load_mcweb_log_cache_from_disk),
            ("load_backup_log_cache_from_disk", _load_backup_log_cache_boot_step),
            ("ensure_session_tracking_initialized", ensure_session_tracking_initialized),
            ("warm_file_page_caches", warm_file_page_caches),
            ("collect_and_publish_metrics", collect_and_publish_metrics),
        ]
        if enable_background_workers and callable(start_worker_loops):
            steps.append(("start_worker_loops", start_worker_loops))
        return steps

    def boot_runtime() -> None:
        nonlocal boot_completed
        if boot_completed or not enable_boot_runtime_tasks:
            return
        with boot_lock:
            if boot_completed or not enable_boot_runtime_tasks:
                return
            for step_name, step_func in _build_boot_steps():
                try:
                    step_func()
                except Exception as exc:
                    log_mcweb_exception(f"boot_step/{step_name}", exc)
                    log_mcweb_log("boot-failed", command=step_name, rejection_message=str(exc)[:500] or "startup step failed")
                    raise
            boot_completed = True

    return boot_runtime
