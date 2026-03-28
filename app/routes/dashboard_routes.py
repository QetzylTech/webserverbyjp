"""Route registration for the shell-first MC web dashboard."""
# mypy: disable-error-code=untyped-decorator
import threading
from typing import Any

from flask import jsonify, redirect, render_template, request, send_from_directory
from app.core import profiling
from app.queries import dashboard_queries as dashboard_queries_service

from app.routes.dashboard_control_routes import register_control_routes
from app.routes.dashboard_file_routes import register_file_routes
from app.routes.dashboard_metrics_routes import register_metrics_routes
from app.routes.dashboard_notifications_routes import register_notification_routes
from app.routes.dashboard_maintenance_api_routes import register_maintenance_routes
from app.routes.panel_settings_routes import register_panel_settings_routes
from app.commands.maintenance_commands import run_cleanup_event_if_enabled
from app.routes.shell_page import render_shell_page as render_shell_page_helper
from app.services import maintenance_state_store as maintenance_state_store_service


def register_routes(app: Any, state: dict[str, Any]) -> None:
    """Register top-level dashboard routes and wire the supporting route modules."""
    restore_pane_alert_lock = threading.Lock()
    restore_pane_alert_active_ref = [False]
    restore_pane_alert_filename_ref = [""]
    restore_pane_alert_ip_ref = [""]
    restore_pane_alert_client_id_ref = [""]

    def _normalized_ip(value: object) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if "," in raw:
            raw = raw.split(",", 1)[0].strip()
        return raw

    def _current_request_ip() -> str:
        current_ip = ""
        try:
            current_ip = _normalized_ip(state["_get_client_ip"]())
        except Exception:
            current_ip = ""
        if not current_ip:
            current_ip = _normalized_ip(request.headers.get("X-Forwarded-For"))
        if not current_ip:
            current_ip = _normalized_ip(request.remote_addr)
        return current_ip

    def _current_request_client_id() -> str:
        return str(request.args.get("client_id", "") or request.headers.get("X-MCWEB-Client-Id", "") or "").strip()

    def _client_is_active(client_id: str) -> bool:
        key = str(client_id or "").strip()
        if not key:
            return False
        registry = state.get("client_registry")
        lock = state.get("client_registry_lock")
        if not isinstance(registry, dict) or lock is None:
            return False
        with lock:
            return isinstance(registry.get(key), dict)

    def _build_nav_alert_state(current_ip: str = "", current_client_id: str = "") -> dict[str, object]:
        with restore_pane_alert_lock:
            active = bool(restore_pane_alert_active_ref[0])
            filename = str(restore_pane_alert_filename_ref[0] or "")
            opener_ip = _normalized_ip(restore_pane_alert_ip_ref[0])
            opener_client_id = str(restore_pane_alert_client_id_ref[0] or "").strip()
            if active and opener_client_id and not _client_is_active(opener_client_id):
                restore_pane_alert_active_ref[0] = False
                restore_pane_alert_filename_ref[0] = ""
                restore_pane_alert_ip_ref[0] = ""
                restore_pane_alert_client_id_ref[0] = ""
                active = False
                filename = ""
                opener_ip = ""
                opener_client_id = ""
        opened_by_self = bool(current_client_id and opener_client_id and current_client_id == opener_client_id)
        if not opened_by_self:
            opened_by_self = bool(current_ip and opener_ip and current_ip == opener_ip)
        opener_identity = opener_ip or "unknown"
        try:
            device_map = state["get_device_name_map"]()
        except Exception:
            device_map = {}
        if isinstance(device_map, dict):
            opener_identity = str(device_map.get(opener_ip, opener_identity) or opener_identity)
        observed = dashboard_queries_service.get_observed_state_model(state).get("observed", {})
        home_attention = dashboard_queries_service.get_home_attention_level(observed)
        cleanup_missed_runs = 0
        try:
            cleanup_missed_runs = int(maintenance_state_store_service.get_cleanup_missed_run_count(state) or 0)
        except Exception:
            cleanup_missed_runs = 0
        cleanup_has_missed = cleanup_missed_runs > 0
        return {
            "restore_pane_attention": bool(active),
            "restore_pane_filename": filename,
            "restore_pane_opened_by_name": opener_identity,
            "restore_pane_opened_by_ip": opener_ip or "unknown",
            "restore_pane_opened_by_self": opened_by_self,
            "home_attention": home_attention,
            "cleanup_has_missed": cleanup_has_missed,
            "cleanup_missed_runs": cleanup_missed_runs,
        }

    def _get_nav_alert_state_from_request() -> dict[str, object]:
        return _build_nav_alert_state(
            current_ip=_current_request_ip(),
            current_client_id=_current_request_client_id(),
        )


    @app.route("/")
    def index() -> Any:
        """Render the persistent dashboard shell or the home fragment payload."""
        home = dashboard_queries_service.get_dashboard_shell_model(state, request.args.get("msg", ""))
        initial_logs: dict[str, str] = {}
        get_log_source_text = state.get("get_log_source_text")
        if callable(get_log_source_text):
            for source in ("minecraft", "backup", "mcweb", "mcweb_log"):
                try:
                    initial_logs[source] = str(get_log_source_text(source) or "")
                except Exception:
                    initial_logs[source] = ""
        return render_shell_page_helper(app, state, render_template, 
            "fragments/home_fragment.html",
            current_page="home",
            page_title="Minecraft Control",
            csrf_token=state["_ensure_csrf_token"](),
            alert_message=home["alert_message"],
            alert_message_code=home["message_code"],
            home_page_heartbeat_interval_ms=state["HOME_PAGE_HEARTBEAT_INTERVAL_MS"],
            metrics_snapshot=state["get_cached_dashboard_metrics"](),
            initial_logs=initial_logs,
        )

    @app.route("/home-heartbeat", methods=["POST"])
    def home_heartbeat() -> tuple[str, int]:
        """Refresh the short-lived activity marker used by the home-page worker."""
        client_id = _current_request_client_id()
        marker = state["_mark_home_page_client_active"]
        try:
            marker(client_id=client_id)
        except TypeError:
            marker()
        return ("", 204)

    @app.route("/ui-error-log", methods=["POST"])
    def ui_error_log() -> tuple[str, int]:
        """Capture client-side modal failures in the server log."""
        payload = request.get_json(silent=True) or {}
        error_code = str(payload.get("error_code", "") or "").strip()
        action = str(payload.get("action", "") or "").strip()
        message = str(payload.get("message", "") or "").strip()

        command = f"{action or 'unknown-action'} | {error_code or 'unknown-error'}"
        state["log_mcweb_log"](
            "ui-error-modal",
            command=command[:200],
            rejection_message=message[:500] if message else "Action Failed modal shown.",
        )
        return ("", 204)

    @app.route("/favicon.ico")
    def favicon() -> Any:
        """Redirect the browser to the configured favicon asset."""
        return redirect(state["FAVICON_URL"])

    @app.route("/sw.js")
    def service_worker() -> Any:
        """Serve root-scoped service worker for offline shell/recovery."""
        response = send_from_directory(str(app.static_folder), "service_worker.js")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.route("/readme")
    def readme_page() -> Any:
        """Render the documentation shell page or its client-side fragment."""
        return render_shell_page_helper(app, state, render_template, 
            "fragments/documentation_fragment.html",
            current_page="readme",
            page_title="README Documentation",
        )

    @app.route("/doc/server_setup_doc.md")
    def readme_markdown() -> Any:
        """Serve the markdown source used by the documentation page."""
        return send_from_directory(str(state["DOCS_DIR"]), "server_setup_doc.md")

    @app.route("/doc/readme-url")
    def readme_url_config() -> Any:
        """Return the configured readme URL used by the documentation shell."""
        return jsonify({"url": state["DOC_README_URL"]})

    @app.route("/observed-state")
    def observed_state() -> Any:
        """Return observed runtime state derived from service/filesystem probes."""
        return jsonify(dashboard_queries_service.get_observed_state_model(state))

    @app.route("/consistency-check")
    def consistency_check() -> Any:
        """Return runtime consistency/invariant report for diagnostics/admin checks."""
        auto_repair_raw = str(request.args.get("auto_repair", "") or "").strip().lower()
        auto_repair = auto_repair_raw in {"1", "true", "yes", "on"}
        if auto_repair:
            sudo_password = request.args.get("sudo_password", "")
            if not state["validate_sudo_password"](sudo_password):
                return state["_password_rejected_response"]()
            state["record_successful_password_ip"]()
        return jsonify(dashboard_queries_service.get_consistency_report_model(state, auto_repair=auto_repair))

    @app.route("/profiling-summary")
    def profiling_summary() -> Any:
        """Return in-process profiling summary when MCWEB_PROFILE is enabled."""
        if not profiling.ENABLED:
            return jsonify({"ok": False, "error": "profiling_disabled", "message": "Profiling is disabled."}), 404
        sudo_password = request.args.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()
        return jsonify({"ok": True, "profiling": profiling.summary()})

    @app.route("/device-name-map")
    def device_name_map() -> Any:
        """Return the current IP-to-device-name mapping for client-side rendering."""
        return jsonify({"map": state["get_device_name_map"]()})

    @app.route("/maintenance/nav-alert/restore-pane-open", methods=["POST"])
    def maintenance_nav_alert_restore_pane_open() -> tuple[str, int]:
        """Record or clear restore-pane activity for cross-client nav attention."""
        payload = request.get_json(silent=True) or {}
        filename = str(payload.get("filename", "") or "").strip()
        opener_client_id = str(payload.get("client_id", "") or "").strip()
        active = payload.get("active", True) is not False
        opener_ip = _current_request_ip() or "unknown"
        with restore_pane_alert_lock:
            if active:
                restore_pane_alert_active_ref[0] = True
                if filename:
                    restore_pane_alert_filename_ref[0] = filename
                restore_pane_alert_ip_ref[0] = opener_ip
                if opener_client_id:
                    restore_pane_alert_client_id_ref[0] = opener_client_id
            else:
                current_client_id = str(restore_pane_alert_client_id_ref[0] or "").strip()
                if not current_client_id or not opener_client_id or current_client_id == opener_client_id:
                    restore_pane_alert_active_ref[0] = False
                    restore_pane_alert_filename_ref[0] = ""
                    restore_pane_alert_ip_ref[0] = ""
                    restore_pane_alert_client_id_ref[0] = ""
        return ("", 204)

    @app.route("/maintenance/nav-alert/state")
    def maintenance_nav_alert_state() -> Any:
        """Return nav attention state for the current request identity."""
        return jsonify({"ok": True, **_get_nav_alert_state_from_request()})

    register_file_routes(app, state)
    register_metrics_routes(app, state, get_nav_alert_state_from_request=_get_nav_alert_state_from_request)
    register_notification_routes(app, state)
    register_maintenance_routes(app, state)
    register_panel_settings_routes(app, state)
    register_control_routes(
        app,
        state,
        run_cleanup_event_if_enabled=run_cleanup_event_if_enabled,
    )




