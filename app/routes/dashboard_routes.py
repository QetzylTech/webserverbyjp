"""Flask route registration for the MC web dashboard."""
import threading
import time

from flask import jsonify, redirect, render_template, request, send_from_directory

from app.routes.dashboard_control_routes import register_control_routes
from app.routes.dashboard_debug_routes import register_debug_routes
from app.routes.dashboard_file_routes import register_file_routes
from app.routes.dashboard_maintenance_api_routes import register_maintenance_routes
from app.services.maintenance_scheduler import run_cleanup_event_if_enabled


def register_routes(app, state):
    """Register all HTTP routes using shared state/functions from mcweb."""
    restore_pane_alert_lock = threading.Lock()
    restore_pane_alert_until_ref = [0.0]
    restore_pane_alert_filename_ref = [""]
    restore_pane_alert_ip_ref = [""]
    restore_pane_alert_client_id_ref = [""]

    def _normalized_ip(value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        if "," in raw:
            raw = raw.split(",", 1)[0].strip()
        return raw

    # Route: /
    @app.route("/")
    def index():
        """Runtime helper index."""
        message_code = request.args.get("msg", "")
        alert_message = ""
        if message_code == "password_incorrect":
            alert_message = "Password incorrect. Action rejected."
        elif message_code == "csrf_invalid":
            alert_message = "Security check failed. Please refresh and try again."
        elif message_code == "session_write_failed":
            alert_message = "Session file write failed."
        elif message_code == "backup_failed":
            alert_message = "Backup failed."
        elif message_code == "internal_error":
            alert_message = "Internal server error."
        elif message_code == "low_storage_space":
            alert_message = state["low_storage_error_message"]()
        elif message_code == "start_failed":
            alert_message = "Server failed to start."

        state["_mark_home_page_client_active"]()
        data = state["get_cached_dashboard_metrics"]()
        if state["is_storage_low"]():
            message_code = "low_storage_space"
            alert_message = state["low_storage_error_message"]()
            data["low_storage_blocked"] = True
            data["low_storage_message"] = alert_message
        return render_template(
            state["HTML_TEMPLATE_NAME"],
            current_page="home",
            service_status=data["service_status"],
            service_status_class=data["service_status_class"],
            service_running_status=data["service_running_status"],
            backups_status=data["backups_status"],
            cpu_per_core_items=data["cpu_per_core_items"],
            cpu_frequency=data["cpu_frequency"],
            cpu_frequency_class=data["cpu_frequency_class"],
            storage_usage=data["storage_usage"],
            storage_usage_class=data["storage_usage_class"],
            players_online=data["players_online"],
            tick_rate=data["tick_rate"],
            session_duration=data["session_duration"],
            idle_countdown=data["idle_countdown"],
            backup_status=data["backup_status"],
            backup_status_class=data["backup_status_class"],
            last_backup_time=data["last_backup_time"],
            next_backup_time=data["next_backup_time"],
            server_time=data["server_time"],
            world_name=data["world_name"],
            ram_usage=data["ram_usage"],
            ram_usage_class=data["ram_usage_class"],
            minecraft_logs_raw=state["get_log_source_text"]("minecraft"),
            rcon_enabled=data["rcon_enabled"],
            csrf_token=state["_ensure_csrf_token"](),
            alert_message=alert_message,
            alert_message_code=message_code,
            home_page_heartbeat_interval_ms=state["HOME_PAGE_HEARTBEAT_INTERVAL_MS"],
        )

    # Route: /home-heartbeat
    @app.route("/home-heartbeat", methods=["POST"])
    def home_heartbeat():
        """Runtime helper home_heartbeat."""
        state["_mark_home_page_client_active"]()
        return ("", 204)

    # Route: /ui-error-log
    @app.route("/ui-error-log", methods=["POST"])
    def ui_error_log():
        """Runtime helper ui_error_log."""
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

    # Route: /favicon.ico
    @app.route("/favicon.ico")
    def favicon():
        """Runtime helper favicon."""
        return redirect(state["FAVICON_URL"])

    # Route: /readme
    @app.route("/readme")
    def readme_page():
        """Runtime helper readme_page."""
        return render_template("documentation.html", current_page="readme")

    register_debug_routes(app, state)

    # Route: /doc/server_setup_doc.md
    @app.route("/doc/server_setup_doc.md")
    def readme_markdown():
        """Runtime helper readme_markdown."""
        return send_from_directory(str(state["DOCS_DIR"]), "server_setup_doc.md")

    # Route: /doc/readme-url
    @app.route("/doc/readme-url")
    def readme_url_config():
        """Runtime helper readme_url_config."""
        return jsonify({"url": state["DOC_README_URL"]})

    # Route: /device-name-map
    @app.route("/device-name-map")
    def device_name_map():
        """Runtime helper device_name_map."""
        return jsonify({"map": state["get_device_name_map"]()})

    # Route: /maintenance/nav-alert/restore-pane-open
    @app.route("/maintenance/nav-alert/restore-pane-open", methods=["POST"])
    def maintenance_nav_alert_restore_pane_open():
        """Record a short-lived restore-pane activity signal for cross-client nav attention."""
        payload = request.get_json(silent=True) or {}
        filename = str(payload.get("filename", "") or "").strip()
        opener_client_id = str(payload.get("client_id", "") or "").strip()
        opener_ip = ""
        try:
            opener_ip = _normalized_ip(state["_get_client_ip"]())
        except Exception:
            opener_ip = ""
        if not opener_ip:
            opener_ip = _normalized_ip(request.headers.get("X-Forwarded-For"))
        if not opener_ip:
            opener_ip = _normalized_ip(request.remote_addr)
        if not opener_ip:
            opener_ip = "unknown"
        now = time.time()
        # Keep alert active while backups clients keep pinging.
        ttl_seconds = 15.0
        with restore_pane_alert_lock:
            restore_pane_alert_until_ref[0] = max(restore_pane_alert_until_ref[0], now + ttl_seconds)
            if filename:
                restore_pane_alert_filename_ref[0] = filename
            restore_pane_alert_ip_ref[0] = opener_ip
            if opener_client_id:
                restore_pane_alert_client_id_ref[0] = opener_client_id
        return ("", 204)

    # Route: /maintenance/nav-alert/state
    @app.route("/maintenance/nav-alert/state")
    def maintenance_nav_alert_state():
        """Return whether restore-pane attention should flash maintenance nav link."""
        now = time.time()
        current_ip = ""
        try:
            current_ip = _normalized_ip(state["_get_client_ip"]())
        except Exception:
            current_ip = ""
        if not current_ip:
            current_ip = _normalized_ip(request.headers.get("X-Forwarded-For")) or _normalized_ip(request.remote_addr)
        current_client_id = str(request.headers.get("X-MCWEB-Client-Id", "") or "").strip()
        with restore_pane_alert_lock:
            active = now <= restore_pane_alert_until_ref[0]
            filename = str(restore_pane_alert_filename_ref[0] or "")
            opener_ip = _normalized_ip(restore_pane_alert_ip_ref[0])
            opener_client_id = str(restore_pane_alert_client_id_ref[0] or "").strip()
        opened_by_self = bool(current_client_id and opener_client_id and current_client_id == opener_client_id)
        if not opened_by_self:
            opened_by_self = bool(current_ip and opener_ip and current_ip == opener_ip)
        device_map = state["get_device_name_map"]()
        opener_name = str(device_map.get(opener_ip, "") or "").strip()
        opener_identity = opener_name or opener_ip or "unknown"
        metrics = state["get_cached_dashboard_metrics"]()
        service_status = str(metrics.get("service_status", "") or "").strip().lower()
        home_attention = "none"
        if service_status == "crashed":
            home_attention = "red"
        elif service_status in {"starting", "shutting down"}:
            home_attention = "yellow"
        return jsonify(
            {
                "ok": True,
                "restore_pane_attention": bool(active),
                "restore_pane_filename": filename,
                "restore_pane_opened_by_name": opener_identity,
                "restore_pane_opened_by_ip": opener_ip or "unknown",
                "restore_pane_opened_by_self": opened_by_self,
                "home_attention": home_attention,
            }
        )

    register_file_routes(app, state)
    register_maintenance_routes(app, state)
    register_control_routes(
        app,
        state,
        run_cleanup_event_if_enabled=run_cleanup_event_if_enabled,
    )
