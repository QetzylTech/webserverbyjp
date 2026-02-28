"""Flask route registration for the MC web dashboard."""

from flask import Response, abort, jsonify, redirect, render_template, request, send_from_directory, stream_with_context
import json
import subprocess


def register_routes(app, state):
    """Register all HTTP routes using shared state/functions from mcweb."""

    @app.route("/")
    def index():
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

        state["_mark_home_page_client_active"]()
        data = state["get_cached_dashboard_metrics"]()
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
            ram_usage=data["ram_usage"],
            ram_usage_class=data["ram_usage_class"],
            minecraft_logs_raw=state["get_log_source_text"]("minecraft"),
            rcon_enabled=data["rcon_enabled"],
            csrf_token=state["_ensure_csrf_token"](),
            alert_message=alert_message,
            alert_message_code=message_code,
            home_page_heartbeat_interval_ms=state["HOME_PAGE_HEARTBEAT_INTERVAL_MS"],
        )

    @app.route("/home-heartbeat", methods=["POST"])
    def home_heartbeat():
        state["_mark_home_page_client_active"]()
        return ("", 204)

    @app.route("/files")
    def files_page():
        return redirect("/backups")

    @app.route("/favicon.ico")
    def favicon():
        return redirect(state["FAVICON_URL"])

    @app.route("/readme")
    def readme_page():
        return render_template("documentation.html", current_page="readme")

    @app.route("/doc/server_setup_doc.md")
    def readme_markdown():
        return send_from_directory(str(state["DOCS_DIR"]), "server_setup_doc.md")

    @app.route("/doc/readme-url")
    def readme_url_config():
        return jsonify({"url": state["DOC_README_URL"]})

    @app.route("/backups")
    def backups_page():
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        return render_template(
            state["FILES_TEMPLATE_NAME"],
            current_page="backups",
            page_title="Backups",
            panel_title="Backups",
            panel_hint="Latest to oldest from /home/marites/backups",
            items=state["get_cached_file_page_items"]("backups"),
            download_base="/download/backups",
            empty_text="No backup zip files found.",
            csrf_token=state["_ensure_csrf_token"](),
            file_page_heartbeat_interval_ms=state["FILE_PAGE_HEARTBEAT_INTERVAL_MS"],
        )

    @app.route("/crash-logs")
    def crash_logs_page():
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        return render_template(
            state["FILES_TEMPLATE_NAME"],
            current_page="crash_logs",
            page_title="Crash Reports",
            panel_title="Crash Reports",
            panel_hint="Latest to oldest from /opt/Minecraft/crash-reports",
            items=state["get_cached_file_page_items"]("crash_logs"),
            download_base="/download/crash-logs",
            empty_text="No crash reports found.",
            csrf_token=state["_ensure_csrf_token"](),
            file_page_heartbeat_interval_ms=state["FILE_PAGE_HEARTBEAT_INTERVAL_MS"],
        )

    @app.route("/minecraft-logs")
    def minecraft_logs_page():
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        return render_template(
            state["FILES_TEMPLATE_NAME"],
            current_page="minecraft_logs",
            page_title="Log Files",
            panel_title="Log Files",
            panel_hint="Latest to oldest from /opt/Minecraft/logs",
            items=state["get_cached_file_page_items"]("minecraft_logs"),
            download_base="/download/minecraft-logs",
            empty_text="No log files (.log/.gz) found.",
            csrf_token=state["_ensure_csrf_token"](),
            file_page_heartbeat_interval_ms=state["FILE_PAGE_HEARTBEAT_INTERVAL_MS"],
        )

    @app.route("/file-page-heartbeat", methods=["POST"])
    def file_page_heartbeat():
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        return ("", 204)

    @app.route("/download/backups/<path:filename>", methods=["POST"])
    def download_backup(filename):
        sudo_password = request.form.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("download-backup", command=filename, rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        safe_name = state["_safe_filename_in_dir"](state["BACKUP_DIR"], filename)
        if safe_name is None:
            state["log_mcweb_action"]("download-backup", command=filename, rejection_message="File not found or invalid path.")
            return abort(404)
        state["log_mcweb_action"]("download-backup", command=safe_name)
        return send_from_directory(str(state["BACKUP_DIR"]), safe_name, as_attachment=True)

    @app.route("/download/crash-logs/<path:filename>")
    def download_crash_log(filename):
        safe_name = state["_safe_filename_in_dir"](state["CRASH_REPORTS_DIR"], filename)
        if safe_name is None:
            return abort(404)
        return send_from_directory(str(state["CRASH_REPORTS_DIR"]), safe_name, as_attachment=True)

    @app.route("/download/minecraft-logs/<path:filename>")
    def download_minecraft_log(filename):
        safe_name = state["_safe_filename_in_dir"](state["MINECRAFT_LOGS_DIR"], filename)
        if safe_name is None:
            return abort(404)
        return send_from_directory(str(state["MINECRAFT_LOGS_DIR"]), safe_name, as_attachment=True)

    @app.route("/log-stream/<source>")
    def log_stream(source):
        settings = state["_log_source_settings"](source)
        if settings is None:
            return Response("invalid log source", status=404)
        state["ensure_log_stream_fetcher_started"](source)
        stream_state = state["log_stream_states"][source]

        def generate():
            state["_increment_log_stream_clients"](source)
            last_seq = 0
            try:
                while True:
                    pending_lines = []
                    with stream_state["cond"]:
                        stream_state["cond"].wait_for(
                            lambda: stream_state["seq"] > last_seq,
                            timeout=state["LOG_STREAM_HEARTBEAT_SECONDS"],
                        )
                        current_seq = stream_state["seq"]
                        if current_seq > last_seq:
                            if stream_state["events"]:
                                first_available = stream_state["events"][0][0]
                                if last_seq < first_available - 1:
                                    last_seq = first_available - 1
                                pending = [(seq, line) for seq, line in stream_state["events"] if seq > last_seq]
                                if pending:
                                    pending_lines = [line for _, line in pending]
                                    last_seq = pending[-1][0]
                            else:
                                last_seq = current_seq

                    if pending_lines:
                        for line in pending_lines:
                            yield f"data: {line}\n\n"
                    else:
                        yield ": keepalive\n\n"
            finally:
                state["_decrement_log_stream_clients"](source)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/log-text/<source>")
    def log_text(source):
        logs = state["get_log_source_text"](source)
        if logs is None:
            return jsonify({"logs": "(no logs)"}), 404
        return jsonify({"logs": logs})

    @app.route("/metrics")
    def metrics():
        return jsonify(state["get_cached_dashboard_metrics"]())

    @app.route("/metrics-stream")
    def metrics_stream():
        def generate():
            with state["metrics_cache_cond"]:
                state["metrics_stream_client_count"] += 1
                state["metrics_cache_cond"].notify_all()
            last_seq = -1
            try:
                while True:
                    with state["metrics_cache_cond"]:
                        state["metrics_cache_cond"].wait_for(
                            lambda: state["metrics_cache_seq"] != last_seq,
                            timeout=state["METRICS_STREAM_HEARTBEAT_SECONDS"],
                        )
                        seq = state["metrics_cache_seq"]
                        snapshot = dict(state["metrics_cache_payload"]) if state["metrics_cache_payload"] else None

                    if snapshot is None:
                        snapshot = state["get_cached_dashboard_metrics"]()
                        with state["metrics_cache_cond"]:
                            seq = state["metrics_cache_seq"]

                    if seq != last_seq and snapshot is not None:
                        payload = json.dumps(snapshot, separators=(",", ":"))
                        yield f"data: {payload}\n\n"
                        last_seq = seq
                    else:
                        yield ": keepalive\n\n"
            finally:
                with state["metrics_cache_cond"]:
                    state["metrics_stream_client_count"] = max(0, state["metrics_stream_client_count"] - 1)
                    state["metrics_cache_cond"].notify_all()

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/start", methods=["POST"])
    def start():
        state["set_service_status_intent"]("starting")
        subprocess.run(["sudo", "systemctl", "start", state["SERVICE"]])
        state["invalidate_status_cache"]()
        if state["write_session_start_time"]() is None:
            state["log_mcweb_action"]("start", rejection_message="Session file write failed.")
            return state["_session_write_failed_response"]()
        state["reset_backup_schedule_state"]()
        state["log_mcweb_action"]("start")
        return state["_ok_response"]()

    @app.route("/stop", methods=["POST"])
    def stop():
        sudo_password = request.form.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("stop", rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()

        state["set_service_status_intent"]("shutting")
        state["graceful_stop_minecraft"]()
        state["clear_session_start_time"]()
        state["reset_backup_schedule_state"]()
        state["log_mcweb_action"]("stop")
        return state["_ok_response"]()

    @app.route("/backup", methods=["POST"])
    def backup():
        if not state["run_backup_script"](trigger="manual"):
            detail = ""
            backup_state = state["backup_state"]
            with backup_state.lock:
                detail = backup_state.last_error
            message = "Backup failed."
            if detail:
                message = f"Backup failed: {detail}"
            state["log_mcweb_action"]("backup", rejection_message=message)
            return state["_backup_failed_response"](message)
        state["log_mcweb_action"]("backup")
        return state["_ok_response"]()

    @app.route("/rcon", methods=["POST"])
    def rcon():
        command = request.form.get("rcon_command", "").strip()
        sudo_password = request.form.get("sudo_password", "")
        if not command:
            state["log_mcweb_action"]("submit", rejection_message="Command is required.")
            return state["_rcon_rejected_response"]("Command is required.", 400)
        if not state["is_rcon_enabled"]():
            state["log_mcweb_action"](
                "submit",
                command=command,
                rejection_message="RCON is disabled: rcon.password not found in server.properties.",
            )
            return state["_rcon_rejected_response"](
                "RCON is disabled: rcon.password not found in server.properties.",
                503,
            )
        if state["get_status"]() != "active":
            state["log_mcweb_action"]("submit", command=command, rejection_message="Server is not running.")
            return state["_rcon_rejected_response"]("Server is not running.", 409)
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("submit", command=command, rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()

        try:
            result = state["_run_mcrcon"](command, timeout=8)
        except Exception as exc:
            state["log_mcweb_exception"]("rcon_execute", exc)
            state["log_mcweb_action"]("submit", command=command, rejection_message="RCON command failed to execute.")
            return state["_rcon_rejected_response"]("RCON command failed to execute.", 500)

        if result.returncode != 0:
            detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
            message = "RCON command failed."
            if detail:
                message = f"RCON command failed: {detail[:400]}"
            state["log_mcweb_action"]("submit", command=command, rejection_message=message)
            return state["_rcon_rejected_response"](message, 500)

        state["log_mcweb_action"]("submit", command=command)
        return state["_ok_response"]()
