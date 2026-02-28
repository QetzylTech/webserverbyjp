"""Flask route registration for the MC web dashboard."""
from flask import jsonify, redirect, render_template, request, send_from_directory

from app.routes.dashboard_control_routes import register_control_routes
from app.routes.dashboard_debug_routes import register_debug_routes
from app.routes.dashboard_file_routes import register_file_routes
from app.routes.dashboard_maintenance_api_routes import register_maintenance_routes
from app.services.maintenance_basics import is_maintenance_allowed
from app.services.maintenance_scheduler import run_cleanup_event_if_enabled


def _dummy_debug_env_rows():
    return [
        {
            "key": "DEV_ENABLED",
            "value": "false",
            "original": "false",
            "overridden": False,
        },
        {
            "key": "DEBUG_ENABLED",
            "value": "false",
            "original": "false",
            "overridden": False,
        },
        {
            "key": "DEBUG_PAGE_VISIBLE",
            "value": "false",
            "original": "false",
            "overridden": False,
        },
        {
            "key": "DISPLAY_TZ",
            "value": "Asia/Manila",
            "original": "Asia/Manila",
            "overridden": False,
        },
        {
            "key": "SERVICE",
            "value": "minecraft",
            "original": "minecraft",
            "overridden": False,
        },
        {
            "key": "RCON_PORT",
            "value": "25575",
            "original": "25575",
            "overridden": False,
        },
        {
            "key": "WORLD_DIR",
            "value": "/opt/Minecraft/server",
            "original": "/opt/Minecraft/server",
            "overridden": False,
        },
    ]


def register_routes(app, state):
    """Register all HTTP routes using shared state/functions from mcweb."""

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

    # Route: /files
    @app.route("/files")
    def files_page():
        """Runtime helper files_page."""
        return redirect("/backups")

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

    register_debug_routes(
        app,
        state,
        is_maintenance_allowed=is_maintenance_allowed,
        dummy_debug_env_rows=_dummy_debug_env_rows,
    )

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

    register_file_routes(app, state)
    register_maintenance_routes(app, state)
    register_control_routes(
        app,
        state,
        run_cleanup_event_if_enabled=run_cleanup_event_if_enabled,
    )
