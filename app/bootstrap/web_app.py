"""Web dashboard for controlling and monitoring a Minecraft server runtime.

This app provides:
- Service controls (start/stop/manual backup)
- Live server and Minecraft stats
- Live control/log viewing
- Automatic idle shutdown and session-based backup scheduling"""

from flask import Flask
import json
import time
from typing import Any

from app.bootstrap import web_app_config
from app.bootstrap import web_app_runtime
from app.bootstrap import web_app_setup
from app.bootstrap import web_app_state
from app.core.action_logging import make_log_action, make_log_exception
from app.routes.dashboard_routes import register_routes
from app.services import app_lifecycle as app_lifecycle_service
from app.services import bootstrap as bootstrap_service
from app.services import dashboard_file_runtime as dashboard_file_runtime_service
from app.services import dashboard_log_runtime as dashboard_log_runtime_service
from app.services import dashboard_metrics_runtime as dashboard_metrics_runtime_service
from app.services import dashboard_operations_runtime as dashboard_operations_runtime_service
from app.services import dashboard_state_runtime as dashboard_state_runtime_service
from app.services import data_bootstrap as data_bootstrap_service
from app.services import minecraft_runtime as minecraft_runtime_service
from app.services import request_bindings as request_bindings_service
from app.services import runtime_bindings as runtime_bindings_service
from app.services import runtime_wiring as runtime_wiring_service
from app.services import session_watchers as session_watchers_service
from app.services import service_ops as service_ops
from app.services import state_builder as state_builder_service
from app.services import status_cache as status_cache_service
from app.services import worker_runtime as worker_runtime_service
from app.services import system_bindings as system_bindings_service
from app.services import world_bindings as world_bindings_service
from app.state import REQUIRED_STATE_KEY_SET

_bootstrap = web_app_config.load_bootstrap_config()
APP_DIR = _bootstrap.app_dir
APP_CONFIG = _bootstrap.app_config
WEB_CONF_PATH = _bootstrap.web_conf_path
_WEB_CFG_VALUES = _bootstrap.raw_values
SETUP_REQUIRED_STATE = _bootstrap.setup_required_state
DISPLAY_TZ = _bootstrap.display_tz

app = Flask(
    __name__,
    template_folder=str(APP_DIR / "templates"),
    static_folder=str(APP_DIR / "static"),
)
web_app_config.configure_flask_app(app, app_config=APP_CONFIG, setup_required_state=SETUP_REQUIRED_STATE)

STATE = None

STATE_VARS = web_app_state.build_state(APP_CONFIG, app_dir=APP_DIR, display_tz=DISPLAY_TZ)
APP_STATE_DB_PATH = STATE_VARS["APP_STATE_DB_PATH"]
DATA_DIR = STATE_VARS["DATA_DIR"]
MCWEB_LOG_DIR = STATE_VARS["MCWEB_LOG_DIR"]
MCWEB_ACTION_LOG_FILE = STATE_VARS["MCWEB_ACTION_LOG_FILE"]
MCWEB_LOG_FILE = STATE_VARS["MCWEB_LOG_FILE"]
PROCESS_ROLE = STATE_VARS["PROCESS_ROLE"]
SERVICE = STATE_VARS["SERVICE"]
BACKUP_SCRIPT = STATE_VARS["BACKUP_SCRIPT"]
BACKUP_LOG_FILE = STATE_VARS["BACKUP_LOG_FILE"]
BACKUP_STATE_FILE = STATE_VARS["BACKUP_STATE_FILE"]
SESSION_FILE = STATE_VARS["SESSION_FILE"]
SERVER_PROPERTIES_CANDIDATES = STATE_VARS["SERVER_PROPERTIES_CANDIDATES"]
backup_state = STATE_VARS["backup_state"]

log_mcweb_action = make_log_action(DISPLAY_TZ, MCWEB_LOG_DIR, MCWEB_ACTION_LOG_FILE)
log_mcweb_log = make_log_action(DISPLAY_TZ, MCWEB_LOG_DIR, MCWEB_LOG_FILE)
log_mcweb_exception = make_log_exception(log_mcweb_log)

_runtime_namespace = {
    **STATE_VARS,
    "log_mcweb_action": log_mcweb_action,
    "log_mcweb_log": log_mcweb_log,
    "log_mcweb_exception": log_mcweb_exception,
}

data_bootstrap_service.ensure_data_bootstrap(
    data_dir=DATA_DIR,
    app_state_db_path=APP_STATE_DB_PATH,
    log_mcweb_log=log_mcweb_log,
    log_mcweb_exception=log_mcweb_exception,
)

_setup_required, _setup_mode = web_app_setup.configure_setup(
    app,
    web_conf_path=WEB_CONF_PATH,
    web_cfg_values=_WEB_CFG_VALUES,
    setup_required_state=SETUP_REQUIRED_STATE,
    data_dir=DATA_DIR,
    app_state_db_path=APP_STATE_DB_PATH,
    log_mcweb_log=log_mcweb_log,
    log_mcweb_exception=log_mcweb_exception,
)

_runtime_bundle = runtime_wiring_service.create_runtime(
    app=app,
    namespace=_runtime_namespace,
    wiring_config=runtime_wiring_service.RuntimeWiringConfig(
        required_state_key_set=REQUIRED_STATE_KEY_SET,
        runtime_context_extra_keys=web_app_runtime.RUNTIME_CONTEXT_EXTRA_KEYS,
        runtime_imported_symbols=web_app_runtime.RUNTIME_IMPORTED_SYMBOLS,
    ),
    services=runtime_wiring_service.RuntimeServices(
        world_bindings_service=world_bindings_service,
        system_bindings_service=system_bindings_service,
        runtime_bindings_service=runtime_bindings_service,
        request_bindings_service=request_bindings_service,
        state_builder_service=state_builder_service,
        app_lifecycle_service=app_lifecycle_service,
        minecraft_runtime_service=minecraft_runtime_service,
        session_watchers_service=session_watchers_service,
        control_plane_service=service_ops,
        dashboard_file_runtime_service=dashboard_file_runtime_service,
        dashboard_log_runtime_service=dashboard_log_runtime_service,
        dashboard_state_runtime_service=dashboard_state_runtime_service,
        dashboard_metrics_runtime_service=dashboard_metrics_runtime_service,
        dashboard_operations_runtime_service=dashboard_operations_runtime_service,
        status_cache_service=status_cache_service,
        worker_runtime_service=worker_runtime_service,
    ),
    register_routes=register_routes,
)
RUNTIME_CONTEXT = _runtime_bundle["runtime_context"]
STATE = _runtime_bundle["state"]
_static_asset_version_fn = _runtime_bundle["static_asset_version_fn"]
_boot_runtime = _runtime_bundle["boot_runtime"]
run_server = _runtime_bundle["run_server"]
if _setup_required():
    def _setup_run_server() -> None:
        bootstrap_service.run_server(
            app,
            APP_CONFIG,
            log_mcweb_log,
            log_mcweb_exception,
            boot_steps=[],
        )
    run_server = _setup_run_server


def ensure_runtime_bootstrapped() -> None:
    """Run non-server boot steps for WSGI/imported app entrypoints."""
    if _setup_required() or PROCESS_ROLE == "worker":
        return
    _boot_runtime()


def run_worker() -> None:
    """Run background worker loops without starting the Flask web server."""
    log_mcweb_log("worker-boot-start", command=f"role={PROCESS_ROLE}")
    ctx = RUNTIME_CONTEXT
    boot_steps = [
        ("ensure_session_tracking_initialized", ctx["ensure_session_tracking_initialized"]),
        ("start_worker_loops", lambda: worker_runtime_service.start_worker_loops(STATE)),
    ]
    for step_name, step_func in boot_steps:
        try:
            step_func()
        except Exception as exc:
            log_mcweb_exception(f"worker_step/{step_name}", exc)
            raise
    log_mcweb_log("worker-boot-ready", command="background loops active")
    while True:
        # Keep worker process alive while daemon loops run.
        time.sleep(60)


@app.context_processor
def inject_asset_helpers() -> dict[str, Any]:
    """Expose per-file static version helper to templates."""
    maintenance_enabled = True
    cleanup_has_missed = False
    try:
        non_normal_path = DATA_DIR / "cleanup_non_normal.txt"
        if non_normal_path.exists():
            payload = json.loads(non_normal_path.read_text(encoding="utf-8"))
            cleanup_has_missed = bool(payload.get("missed_runs"))
    except Exception:
        cleanup_has_missed = False
    return {
        "static_version": _static_asset_version_fn,
        "maintenance_enabled": maintenance_enabled,
        "cleanup_has_missed": cleanup_has_missed,
    }


if __name__ == "__main__":
    if PROCESS_ROLE == "worker":
        run_worker()
    else:
        run_server()
