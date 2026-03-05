"""Standalone debug Flask app that reuses main runtime services."""

from __future__ import annotations

import threading
from pathlib import Path

from flask import Flask, redirect, request, send_from_directory

from app.bootstrap import web_app as main_runtime
from app.core.action_logging import make_log_action
from app.core.config import apply_default_flask_config
from debug.bindings import build_debug_bindings
from debug.routes import register_debug_routes
from debug import tools as debug_tools_service
from debug.server_properties import (
    DEBUG_SERVER_PROPERTIES_BOOL_KEYS,
    DEBUG_SERVER_PROPERTIES_ENUMS,
    DEBUG_SERVER_PROPERTIES_INT_KEYS,
    DEBUG_SERVER_PROPERTIES_KEYS,
)

DEBUG_DIR = Path(__file__).resolve().parent
app = Flask(
    __name__,
    template_folder=str(DEBUG_DIR / "templates"),
    static_folder=str(main_runtime.APP_DIR / "static"),
)
app.config["SECRET_KEY"] = main_runtime.app.config.get("SECRET_KEY")
apply_default_flask_config(app)

_runtime_ctx = dict(main_runtime.RUNTIME_CONTEXT)
_debug_env_lock = threading.Lock()
_debug_env_original_values = dict(getattr(main_runtime, "_WEB_CFG_VALUES", {}))
_debug_env_overrides = {}
_log_debug_page_action = make_log_action(
    main_runtime.DISPLAY_TZ,
    main_runtime.MCWEB_LOG_DIR,
    main_runtime.MCWEB_LOG_DIR / "debug_page.log",
)

_bindings = build_debug_bindings(
    debug_tools_service=debug_tools_service,
    debug_enabled=True,
    debug_world_name="debug_world",
    debug_motd="debugging in progress",
    data_dir=main_runtime.DATA_DIR,
    app_dir=main_runtime.APP_DIR,
    service=main_runtime.SERVICE,
    backup_script=main_runtime.BACKUP_SCRIPT,
    backup_log_file=main_runtime.BACKUP_LOG_FILE,
    mcweb_action_log_file=main_runtime.MCWEB_ACTION_LOG_FILE,
    backup_state_file=main_runtime.BACKUP_STATE_FILE,
    session_file=main_runtime.SESSION_FILE,
    server_properties_candidates=main_runtime.SERVER_PROPERTIES_CANDIDATES,
    debug_server_properties_keys=DEBUG_SERVER_PROPERTIES_KEYS,
    debug_server_properties_forced_values={"level-name": "debug_world"},
    debug_server_properties_int_keys=DEBUG_SERVER_PROPERTIES_INT_KEYS,
    debug_server_properties_bool_keys=DEBUG_SERVER_PROPERTIES_BOOL_KEYS,
    debug_server_properties_enums=DEBUG_SERVER_PROPERTIES_ENUMS,
    debug_env_lock=_debug_env_lock,
    debug_env_original_values=_debug_env_original_values,
    debug_env_overrides=_debug_env_overrides,
    backup_state=main_runtime.backup_state,
    app=app,
    namespace=_runtime_ctx,
    log_mcweb_log=main_runtime.log_mcweb_log,
    log_mcweb_exception=main_runtime.log_mcweb_exception,
    log_debug_page_action=_log_debug_page_action,
    refresh_world_dir=_runtime_ctx["_refresh_world_dir_from_server_properties"],
    refresh_rcon_config=_runtime_ctx["_refresh_rcon_config"],
    invalidate_status_cache=_runtime_ctx["invalidate_status_cache"],
    set_service_status_intent=_runtime_ctx["set_service_status_intent"],
    write_session_start_time=_runtime_ctx["write_session_start_time"],
    start_service_non_blocking=_runtime_ctx["start_service_non_blocking"],
    validate_sudo_password=_runtime_ctx["validate_sudo_password"],
    record_successful_password_ip=_runtime_ctx["record_successful_password_ip"],
    graceful_stop_minecraft=_runtime_ctx["graceful_stop_minecraft"],
    clear_session_start_time=_runtime_ctx["clear_session_start_time"],
    reset_backup_schedule_state=_runtime_ctx["reset_backup_schedule_state"],
    run_backup_script=_runtime_ctx["run_backup_script"],
)

DEBUG_STATE = {
    "DEBUG_ENABLED": True,
    "DEBUG_PAGE_VISIBLE": True,
    "DEBUG_SERVER_PROPERTIES_KEYS": DEBUG_SERVER_PROPERTIES_KEYS,
    "_ensure_csrf_token": _runtime_ctx["_ensure_csrf_token"],
    "_is_csrf_valid": _runtime_ctx["_is_csrf_valid"],
    "_csrf_rejected_response": _runtime_ctx["_csrf_rejected_response"],
    "validate_sudo_password": _runtime_ctx["validate_sudo_password"],
    "record_successful_password_ip": _runtime_ctx["record_successful_password_ip"],
    "backup_state": main_runtime.backup_state,
    "is_storage_low": _runtime_ctx["is_storage_low"],
    "low_storage_error_message": _runtime_ctx["low_storage_error_message"],
    "debug_env_original_values": _debug_env_original_values,
    "log_debug_page_action": _log_debug_page_action,
    **_bindings,
}


def _static_version(filename: str) -> int:
    target = DEBUG_DIR / "static" / filename
    try:
        return int(target.stat().st_mtime_ns)
    except OSError:
        return 0


@app.before_request
def _csrf_guard():
    DEBUG_STATE["_ensure_csrf_token"]()
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.path != "/debug"
        and not DEBUG_STATE["_is_csrf_valid"]()
    ):
        return DEBUG_STATE["_csrf_rejected_response"]()
    return None


@app.context_processor
def inject_helpers():
    return {"static_version": _static_version, "maintenance_enabled": False, "cleanup_has_missed": False}


@app.route("/")
def _root():
    return redirect("/debug")


@app.route("/debug-static/<path:filename>")
def debug_static(filename):
    return send_from_directory(str(DEBUG_DIR / "static"), filename)


register_debug_routes(app, DEBUG_STATE)


def run_server():
    host = str(getattr(main_runtime, "DEBUG_APP_HOST", "127.0.0.1") or "127.0.0.1")
    try:
        port = max(1, int(getattr(main_runtime, "DEBUG_APP_PORT", 8765)))
    except Exception:
        port = 8765
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_server()
