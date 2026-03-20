"""Setup wiring for initial configuration flow."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from flask import abort, redirect, request

from app.routes.setup_routes import register_setup_routes
from app.services import data_bootstrap as data_bootstrap_service
from app.services import setup_orchestration as setup_orchestration_service
from app.services import setup_service
from app.services.worker_scheduler import start_detached


def configure_setup(
    app: Any,
    *,
    web_conf_path: str | Path,
    web_cfg_values: dict[str, object],
    setup_required_state: dict[str, object],
    data_dir: str | Path,
    app_state_db_path: str | Path,
    log_mcweb_log: Callable[..., object],
    log_mcweb_exception: Callable[..., object],
) -> tuple[Callable[[], bool], Callable[[], str]]:
    def _setup_required() -> bool:
        return bool(setup_required_state.get("required"))

    def _setup_mode() -> str:
        return str(setup_required_state.get("mode", "full") or "full")

    def _trigger_process_reload() -> None:
        def _reload() -> None:
            time.sleep(0.35)
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as exc:
                log_mcweb_exception("setup/reload", exc)

        start_detached(target=_reload, daemon=True)

    def _setup_defaults() -> dict[str, object]:
        return setup_service.setup_form_defaults(web_cfg_values)

    def _save_setup_values(values: dict[str, object]) -> tuple[bool, str, dict[str, str]]:
        return setup_orchestration_service.save_setup_values(
            values,
            setup_service=setup_service,
            data_bootstrap_service=data_bootstrap_service,
            web_conf_path=web_conf_path,
            data_dir=data_dir,
            app_state_db_path=app_state_db_path,
            setup_required_state=setup_required_state,
            trigger_process_reload=_trigger_process_reload,
            log_mcweb_log=log_mcweb_log,
            log_mcweb_exception=log_mcweb_exception,
        )

    register_setup_routes(
        app,
        is_setup_required=_setup_required,
        setup_mode=_setup_mode,
        setup_defaults=_setup_defaults,
        save_setup_values=_save_setup_values,
    )

    def _setup_route_guard() -> Any:
        setup_mode = _setup_required()
        path = request.path or ""
        if setup_mode:
            if path == "/setup" or path.startswith("/setup") or path.startswith("/static/") or path == "/sw.js":
                return None
            if path == "/favicon.ico":
                return None
            return redirect("/setup")
        if path == "/setup" or path.startswith("/setup"):
            return None
        return None

    app.before_request(_setup_route_guard)

    return _setup_required, _setup_mode
