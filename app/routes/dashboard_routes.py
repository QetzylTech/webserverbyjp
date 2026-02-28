"""Flask route registration for the MC web dashboard."""

from flask import Response, abort, jsonify, redirect, render_template, request, send_from_directory, stream_with_context
import json
import subprocess
import gzip
import threading
from collections import deque


def register_routes(app, state):
    """Register all HTTP routes using shared state/functions from mcweb."""

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

    @app.route("/home-heartbeat", methods=["POST"])
    def home_heartbeat():
        """Runtime helper home_heartbeat."""
        state["_mark_home_page_client_active"]()
        return ("", 204)

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

    @app.route("/files")
    def files_page():
        """Runtime helper files_page."""
        return redirect("/backups")

    @app.route("/favicon.ico")
    def favicon():
        """Runtime helper favicon."""
        return redirect(state["FAVICON_URL"])

    @app.route("/readme")
    def readme_page():
        """Runtime helper readme_page."""
        return render_template("documentation.html", current_page="readme")

    @app.route("/debug")
    def debug_page():
        """Runtime helper debug_page."""
        if not state["DEBUG_ENABLED"]:
            return abort(404)
        debug_message = (request.args.get("msg", "") or "").strip()
        props = state["get_debug_server_properties_rows"]()
        editor_path = props.get("path", "server.properties")
        return render_template(
            "debug.html",
            current_page="debug",
            debug_rows=state["get_debug_env_rows"](),
            csrf_token=state["_ensure_csrf_token"](),
            debug_message=debug_message,
            debug_server_properties_path=editor_path,
        )

    @app.route("/debug/server-properties")
    def debug_server_properties_get():
        """Runtime helper debug_server_properties_get."""
        if not state["DEBUG_ENABLED"]:
            return abort(404)
        payload = state["get_debug_server_properties_rows"]()
        status = 200 if payload.get("ok") else 500
        return jsonify(payload), status

    @app.route("/debug/server-properties", methods=["POST"])
    def debug_server_properties_set():
        """Runtime helper debug_server_properties_set."""
        if not state["DEBUG_ENABLED"]:
            return abort(404)
        sudo_password = request.form.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            state["log_debug_page_action"]("debug-server-properties-save", rejection_message="Password incorrect.")
            return jsonify({
                "ok": False,
                "error": "password_incorrect",
                "message": "Password incorrect. Action rejected.",
                "errors": [],
            }), 403
        state["record_successful_password_ip"]()
        values = {}
        for key in state["DEBUG_SERVER_PROPERTIES_KEYS"]:
            values[key] = request.form.get(f"prop_{key}", "")
        result = state["set_debug_server_properties_values"](values)
        if not result.get("ok"):
            state["log_debug_page_action"]("debug-server-properties-save", rejection_message=result.get("message", "Save failed."))
            return jsonify({
                "ok": False,
                "message": result.get("message", "Save failed."),
                "errors": result.get("errors", []),
            }), 400
        state["log_debug_page_action"]("debug-server-properties-save", command=result.get("path", "server.properties"))
        return jsonify({"ok": True, "path": result.get("path", "server.properties")})

    @app.route("/debug/env", methods=["POST"])
    def debug_env_update():
        """Runtime helper debug_env_update."""
        if not state["DEBUG_ENABLED"]:
            return abort(404)
        sudo_password = request.form.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            state["log_debug_page_action"]("debug-env", rejection_message="Password incorrect.")
            return redirect("/debug?msg=Password+incorrect")
        state["record_successful_password_ip"]()
        action = (request.form.get("action", "apply") or "apply").strip()
        if action == "reset_all":
            state["reset_all_debug_overrides"]()
            state["log_debug_page_action"]("debug-env", command="reset_all")
            return redirect("/debug?msg=All+values+reset+to+mcweb.env")

        updates = {}
        for key in state["debug_env_original_values"].keys():
            updates[key] = request.form.get(f"env_{key}", "")
        errors = state["apply_debug_env_overrides"](updates)
        if errors:
            state["log_debug_page_action"]("debug-env", command="apply", rejection_message="; ".join(errors)[:500])
            return redirect("/debug?msg=Some+values+failed+to+apply")
        state["log_debug_page_action"]("debug-env", command="apply")
        return redirect("/debug?msg=Session+overrides+applied")

    @app.route("/debug/explorer/list")
    def debug_explorer_list():
        """Runtime helper debug_explorer_list."""
        if not state["DEBUG_ENABLED"]:
            return abort(404)
        root = (request.args.get("root", "") or "").strip()
        rel_path = request.args.get("path", "") or ""
        payload = state["debug_explorer_list"](root, rel_path)
        status = 200 if payload.get("ok") else 400
        return jsonify(payload), status

    @app.route("/debug/start", methods=["POST"])
    def debug_start():
        """Runtime helper debug_start."""
        if not state["DEBUG_ENABLED"]:
            return abort(404)
        if state["is_storage_low"]():
            state["log_debug_page_action"]("debug-start", rejection_message=state["low_storage_error_message"]())
            return redirect("/debug?msg=Start+blocked:+low+storage+space")
        ok = state["debug_start_service"]()
        if not ok:
            state["log_debug_page_action"]("debug-start", rejection_message="Session file write failed.")
            return redirect("/debug?msg=Start+failed:+session+file+write+failed")
        state["log_debug_page_action"]("debug-start")
        return redirect("/debug?msg=Start+triggered")

    @app.route("/debug/backup", methods=["POST"])
    def debug_backup():
        """Runtime helper debug_backup."""
        if not state["DEBUG_ENABLED"]:
            return abort(404)
        mode = (request.form.get("mode", "manual") or "manual").strip().lower()
        if mode == "scheduled":
            minutes = (request.form.get("minutes_from_now", "") or "").strip()
            ok, message = state["debug_schedule_backup"](minutes, trigger="manual")
            if not ok:
                state["log_debug_page_action"]("debug-backup", command="scheduled", rejection_message=message)
                return redirect("/debug?msg=Scheduled+backup+failed")
            state["log_debug_page_action"]("debug-backup", command=f"scheduled after={minutes}m")
            return redirect("/debug?msg=Scheduled+backup+registered")

        trigger = "auto" if mode == "auto" else "manual"
        if not state["debug_run_backup"](trigger=trigger):
            detail = ""
            backup_state = state["backup_state"]
            with backup_state.lock:
                detail = backup_state.last_error
            message = "Backup failed."
            if detail:
                message = f"Backup failed: {detail}"
            state["log_debug_page_action"]("debug-backup", command=f"mode={mode}", rejection_message=message)
            return redirect("/debug?msg=Backup+failed")
        state["log_debug_page_action"]("debug-backup", command=f"mode={mode}")
        return redirect("/debug?msg=Backup+triggered")

    @app.route("/debug/stop", methods=["POST"])
    def debug_stop():
        """Runtime helper debug_stop."""
        if not state["DEBUG_ENABLED"]:
            return abort(404)
        sudo_password = request.form.get("sudo_password", "")
        ok, message = state["debug_stop_service"](sudo_password)
        if not ok:
            state["log_debug_page_action"]("debug-stop", rejection_message=message or "Stop failed.")
            return redirect("/debug?msg=Stop+failed")
        state["log_debug_page_action"]("debug-stop")
        return redirect("/debug?msg=Stop+triggered")

    @app.route("/doc/server_setup_doc.md")
    def readme_markdown():
        """Runtime helper readme_markdown."""
        return send_from_directory(str(state["DOCS_DIR"]), "server_setup_doc.md")

    @app.route("/doc/readme-url")
    def readme_url_config():
        """Runtime helper readme_url_config."""
        return jsonify({"url": state["DOC_README_URL"]})

    @app.route("/device-name-map")
    def device_name_map():
        """Runtime helper device_name_map."""
        return jsonify({"map": state["get_device_name_map"]()})

    @app.route("/backups")
    def backups_page():
        """Runtime helper backups_page."""
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
        """Runtime helper crash_logs_page."""
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
        """Runtime helper minecraft_logs_page."""
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
        """Runtime helper file_page_heartbeat."""
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        return ("", 204)

    @app.route("/download/backups/<path:filename>", methods=["POST"])
    def download_backup(filename):
        """Runtime helper download_backup."""
        sudo_password = request.form.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("download-backup", command=filename, rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()
        safe_name = state["_safe_filename_in_dir"](state["BACKUP_DIR"], filename)
        if safe_name is None:
            state["log_mcweb_action"]("download-backup", command=filename, rejection_message="File not found or invalid path.")
            return abort(404)
        state["log_mcweb_action"]("download-backup", command=safe_name)
        return send_from_directory(str(state["BACKUP_DIR"]), safe_name, as_attachment=True)

    @app.route("/download/crash-logs/<path:filename>")
    def download_crash_log(filename):
        """Runtime helper download_crash_log."""
        safe_name = state["_safe_filename_in_dir"](state["CRASH_REPORTS_DIR"], filename)
        if safe_name is None:
            return abort(404)
        return send_from_directory(str(state["CRASH_REPORTS_DIR"]), safe_name, as_attachment=True)

    @app.route("/download/minecraft-logs/<path:filename>")
    def download_minecraft_log(filename):
        """Runtime helper download_minecraft_log."""
        safe_name = state["_safe_filename_in_dir"](state["MINECRAFT_LOGS_DIR"], filename)
        if safe_name is None:
            return abort(404)
        return send_from_directory(str(state["MINECRAFT_LOGS_DIR"]), safe_name, as_attachment=True)

    @app.route("/view-file/<source>/<path:filename>")
    def view_file(source, filename):
        """Runtime helper view_file."""
        source_map = {
            "crash_logs": state["CRASH_REPORTS_DIR"],
            "minecraft_logs": state["MINECRAFT_LOGS_DIR"],
        }
        base_dir = source_map.get((source or "").strip())
        if base_dir is None:
            return jsonify({"ok": False, "message": "Invalid file source."}), 404

        safe_name = state["_safe_filename_in_dir"](base_dir, filename)
        if safe_name is None:
            return jsonify({"ok": False, "message": "File not found."}), 404

        file_path = base_dir / safe_name
        max_bytes = 2_000_000
        try:
            if safe_name.lower().endswith(".gz"):
                # Stream-decompress and keep only the tail window to avoid loading huge files in memory.
                tail_chunks = deque()
                tail_len = 0
                truncated = False
                with gzip.open(file_path, "rt", encoding="utf-8", errors="ignore") as f:
                    while True:
                        chunk = f.read(64 * 1024)
                        if not chunk:
                            break
                        tail_chunks.append(chunk)
                        tail_len += len(chunk)
                        while tail_len > max_bytes and tail_chunks:
                            truncated = True
                            overflow = tail_len - max_bytes
                            head = tail_chunks[0]
                            if len(head) <= overflow:
                                tail_len -= len(head)
                                tail_chunks.popleft()
                            else:
                                tail_chunks[0] = head[overflow:]
                                tail_len -= overflow
                text = "".join(tail_chunks)
                if truncated:
                    text = f"[truncated to last {max_bytes} characters]\n{text}"
            else:
                size = file_path.stat().st_size
                if size > max_bytes:
                    with file_path.open("rb") as f:
                        f.seek(max(0, size - max_bytes))
                        raw = f.read(max_bytes)
                    text = "[truncated to last 2000000 bytes]\n" + raw.decode("utf-8", errors="ignore")
                else:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return jsonify({"ok": False, "message": "Unable to read file."}), 500

        return jsonify({"ok": True, "filename": safe_name, "content": text})

    @app.route("/log-stream/<source>")
    def log_stream(source):
        """Runtime helper log_stream."""
        settings = state["_log_source_settings"](source)
        if settings is None:
            return Response("invalid log source", status=404)
        source_key = settings["source"]
        state["ensure_log_stream_fetcher_started"](source_key)
        stream_state = state["log_stream_states"][source_key]

        def generate():
            """Runtime helper generate."""
            state["_increment_log_stream_clients"](source_key)
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
                state["_decrement_log_stream_clients"](source_key)

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
        """Runtime helper log_text."""
        logs = state["get_log_source_text"](source)
        if logs is None:
            return jsonify({"logs": "(no logs)"}), 404
        return jsonify({"logs": logs})

    @app.route("/metrics")
    def metrics():
        """Runtime helper metrics."""
        return jsonify(state["get_cached_dashboard_metrics"]())

    @app.route("/metrics-stream")
    def metrics_stream():
        """Runtime helper metrics_stream."""
        def generate():
            """Runtime helper generate."""
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
        """Runtime helper start."""
        if state["is_storage_low"]():
            message = state["low_storage_error_message"]()
            state["log_mcweb_action"]("start", rejection_message=message)
            return state["_low_storage_blocked_response"](message)
        state["set_service_status_intent"]("starting")
        state["invalidate_status_cache"]()
        if state["write_session_start_time"]() is None:
            state["log_mcweb_action"]("start", rejection_message="Session file write failed.")
            return state["_session_write_failed_response"]()
        state["reset_backup_schedule_state"]()

        service_name = state["SERVICE"]

        def _start_worker():
            try:
                result = subprocess.run(
                    ["sudo", "systemctl", "start", "--no-block", service_name],
                    capture_output=True,
                    text=True,
                    timeout=12,
                )
            except subprocess.TimeoutExpired:
                state["set_service_status_intent"](None)
                state["invalidate_status_cache"]()
                state["log_mcweb_action"](
                    "start-worker",
                    rejection_message="Failed to start service: timed out issuing non-blocking start.",
                )
                return
            if result.returncode != 0:
                detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
                message = "Failed to start service."
                if detail:
                    message = f"Failed to start service: {detail[:400]}"
                state["set_service_status_intent"](None)
                state["invalidate_status_cache"]()
                state["log_mcweb_action"]("start-worker", rejection_message=message)
                return
            state["invalidate_status_cache"]()

        threading.Thread(target=_start_worker, daemon=True).start()
        state["log_mcweb_action"]("start")
        return state["_ok_response"]()

    @app.route("/stop", methods=["POST"])
    def stop():
        """Runtime helper stop."""
        sudo_password = request.form.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("stop", rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()

        state["set_service_status_intent"]("shutting")
        state["graceful_stop_minecraft"]()
        state["clear_session_start_time"]()
        state["reset_backup_schedule_state"]()
        state["log_mcweb_action"]("stop")
        return state["_ok_response"]()

    @app.route("/backup", methods=["POST"])
    def backup():
        """Runtime helper backup."""
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

    @app.route("/restore-backup", methods=["POST"])
    def restore_backup():
        """Runtime helper restore_backup."""
        sudo_password = request.form.get("sudo_password", "")
        filename = (request.form.get("filename", "") or "").strip()

        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("restore-backup", command=filename, rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()
        if not filename:
            return jsonify({"ok": False, "error": "restore_failed", "message": "Backup filename is required."}), 400

        result = state["start_restore_job"](filename)
        if not result.get("ok"):
            message = result.get("message", "Restore failed to start.")
            state["log_mcweb_action"]("restore-backup", command=filename, rejection_message=message)
            return jsonify({"ok": False, "error": "restore_failed", "message": message}), 409

        state["log_mcweb_action"]("restore-backup", command=f"{filename} (started)")
        return jsonify({
            "ok": True,
            "message": "Restore started.",
            "job_id": result.get("job_id", ""),
        })

    @app.route("/restore-status")
    def restore_status():
        """Runtime helper restore_status."""
        since = request.args.get("since", "0")
        job_id = (request.args.get("job_id", "") or "").strip() or None
        payload = state["get_restore_status"](since_seq=since, job_id=job_id)
        return jsonify(payload)

    @app.route("/undo-restore", methods=["POST"])
    def undo_restore():
        """Runtime helper undo_restore."""
        sudo_password = request.form.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("undo-restore", rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()

        result = state["start_undo_restore_job"]()
        if not result.get("ok"):
            message = result.get("message", "Undo restore failed to start.")
            state["log_mcweb_action"]("undo-restore", rejection_message=message)
            return jsonify({"ok": False, "error": "undo_restore_failed", "message": message}), 409

        state["log_mcweb_action"]("undo-restore", command="started")
        return jsonify({
            "ok": True,
            "message": "Undo restore started.",
            "job_id": result.get("job_id", ""),
        })

    @app.route("/rcon", methods=["POST"])
    def rcon():
        """Runtime helper rcon."""
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
        state["record_successful_password_ip"]()

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
