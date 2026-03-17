"""File and log routes for the shell-first MC web dashboard."""
import time

from flask import Response, abort, after_this_request, jsonify, redirect, render_template, request, send_file, send_from_directory, stream_with_context, url_for
from app.core import state_store as state_store_service
from app.commands import snapshot_commands
from app.queries import dashboard_file_queries as file_queries
from app.routes.shell_page import render_shell_page as render_shell_page_helper

def _sse_response(generator):
    return Response(
        stream_with_context(generator),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def register_file_routes(app, state):
    """Register file browsing and log streaming routes."""

    # Route: /backups
    @app.route("/backups")
    def backups_page():
        """Render the backup page shell or backup fragment payload."""
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        return render_shell_page_helper(app, state, render_template, 
            "fragments/files_fragment.html",
            current_page="backups",
            page_title="Backup & Restore",
            panel_title="Backup & Restore",
            panel_hint="Latest to oldest from backup zips and auto snapshots",
            items=[],
            download_base="/download/backups",
            empty_text="No backups or snapshots found.",
            list_api_path="/file-page-items/backups",
            csrf_token=state["_ensure_csrf_token"](),
            file_page_heartbeat_interval_ms=state["FILE_PAGE_HEARTBEAT_INTERVAL_MS"],
            file_page_refresh_interval_ms=int(float(state["FILE_PAGE_CACHE_REFRESH_SECONDS"]) * 1000),
        )

    # Route: /crash-logs
    @app.route("/crash-logs")
    def crash_logs_page():
        """Redirect the retired crash-report page to the unified log browser."""
        return redirect(url_for("minecraft_logs_page", source="crash"))

    # Route: /minecraft-logs
    @app.route("/minecraft-logs")
    def minecraft_logs_page():
        """Render the unified log-browser shell or fragment payload."""
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        initial_log_source = str(request.args.get("source", "minecraft") or "minecraft").strip().lower()
        if file_queries.log_file_source_spec(state, initial_log_source) is None:
            initial_log_source = "minecraft"
        return render_shell_page_helper(app, state, render_template, 
            "fragments/files_fragment.html",
            current_page="minecraft_logs",
            page_title="Log Files",
            panel_title="Log Files",
            panel_hint="Select a log source to browse recent files.",
            items=[],
            download_base="/download/minecraft-logs",
            empty_text="No log files found.",
            list_api_path="/log-files/minecraft",
            csrf_token=state["_ensure_csrf_token"](),
            file_page_heartbeat_interval_ms=state["FILE_PAGE_HEARTBEAT_INTERVAL_MS"],
            file_page_refresh_interval_ms=int(float(state["FILE_PAGE_CACHE_REFRESH_SECONDS"]) * 1000),
            initial_log_source=initial_log_source,
        )


    # Route: /file-page-heartbeat
    @app.route("/file-page-heartbeat", methods=["POST"])
    def file_page_heartbeat():
        """Refresh the activity marker used by the file-page cache worker."""
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        return ("", 204)

    # Route: /file-page-items/<page_name>
    @app.route("/file-page-items/<page_name>")
    def file_page_items(page_name):
        """Return one shell-hydration payload for backup or crash-log file pages."""
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        normalized = str(page_name or "").strip().lower()
        payloads = {
            "backups": {
                "items": state["get_cached_file_page_items"]("backups"),
                "download_base": "/download/backups",
                "view_base": "",
            },
            "crash_logs": {
                "items": state["get_cached_file_page_items"]("crash_logs"),
                "download_base": "/download/crash-logs",
                "view_base": "/view-file/crash_logs",
            },
        }
        payload = payloads.get(normalized)
        if payload is None:
            return jsonify({"ok": False, "message": "Invalid file page source."}), 404
        return jsonify({"ok": True, "page": normalized, **payload})

    # Route: /download/backups/<path:filename>
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

    # Route: /download/backups-snapshot/<path:snapshot_name>
    @app.route("/download/backups-snapshot/<path:snapshot_name>", methods=["POST"])
    def download_snapshot(snapshot_name):
        """Zip one snapshot directory and download it as an attachment."""
        sudo_password = request.form.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("download-snapshot", command=snapshot_name, rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()

        snapshot_dir, safe_name = file_queries.resolve_snapshot_dir(state, snapshot_name)
        if snapshot_dir is None:
            state["log_mcweb_action"]("download-snapshot", command=snapshot_name, rejection_message="Snapshot not found or invalid path.")
            return abort(404)

        try:
            zip_path, tmp_root = snapshot_commands.build_snapshot_archive(snapshot_dir, safe_name)

            @after_this_request
            def _cleanup_temp_zip(response):
                snapshot_commands.cleanup_snapshot_archive(tmp_root)
                return response

            state["log_mcweb_action"]("download-snapshot", command=safe_name)
            return send_file(
                str(zip_path),
                as_attachment=True,
                download_name=f"{safe_name}.zip",
                mimetype="application/zip",
            )
        except OSError:
            state["log_mcweb_action"]("download-snapshot", command=safe_name, rejection_message="Unable to create snapshot zip.")
            return abort(500)

    # Route: /download/crash-logs/<path:filename>
    @app.route("/download/crash-logs/<path:filename>")
    def download_crash_log(filename):
        """Runtime helper download_crash_log."""
        safe_name = state["_safe_filename_in_dir"](state["CRASH_REPORTS_DIR"], filename)
        if safe_name is None:
            return abort(404)
        return send_from_directory(str(state["CRASH_REPORTS_DIR"]), safe_name, as_attachment=True)

    # Route: /download/minecraft-logs/<path:filename>
    @app.route("/download/minecraft-logs/<path:filename>")
    def download_minecraft_log(filename):
        """Runtime helper download_minecraft_log."""
        safe_name = state["_safe_filename_in_dir"](state["MINECRAFT_LOGS_DIR"], filename)
        if safe_name is None:
            return abort(404)
        return send_from_directory(str(state["MINECRAFT_LOGS_DIR"]), safe_name, as_attachment=True)

    # Route: /download/log-files/<source>/<path:filename>
    @app.route("/download/log-files/<source>/<path:filename>")
    def download_log_file(source, filename):
        """Download one non-minecraft log file by source key."""
        spec, safe_name = file_queries.resolve_log_file(state, source, filename)
        if spec is None or safe_name is None:
            return abort(404)
        return send_from_directory(str(spec["base_dir"]), safe_name, as_attachment=True)

    # Route: /log-files/<source>
    @app.route("/log-files/<source>")
    def list_log_files(source):
        """Return one log-file inventory payload for the shell-hydrated log browser."""
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        spec = file_queries.log_file_source_spec(state, source)
        if spec is None:
            return jsonify({"ok": False, "message": "Invalid log file source."}), 404
        if spec["key"] == "minecraft":
            items = state["get_cached_file_page_items"]("minecraft_logs")
        elif spec["key"] == "crash":
            items = state["get_cached_file_page_items"]("crash_logs")
        else:
            items = file_queries.log_file_items_from_spec(state, spec)
        return jsonify(
            {
                "ok": True,
                "source": spec["key"],
                "items": items,
                "download_base": spec["download_base"],
                "view_base": spec["view_base"],
            }
        )

    # Route: /view-file/<source>/<path:filename>
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
        text, error_message = file_queries.read_view_file_content(file_path, safe_name)
        if error_message:
            return jsonify({"ok": False, "message": error_message}), 500

        return jsonify({"ok": True, "filename": safe_name, "content": text})

    # Route: /view-log-file/<source>/<path:filename>
    @app.route("/view-log-file/<source>/<path:filename>")
    def view_log_file(source, filename):
        """View one non-minecraft log file by source key."""
        spec, safe_name = file_queries.resolve_log_file(state, source, filename)
        if spec is None:
            return jsonify({"ok": False, "message": "Invalid log file source."}), 404
        if safe_name is None:
            return jsonify({"ok": False, "message": "File not found."}), 404

        file_path = spec["base_dir"] / safe_name
        text, error_message = file_queries.read_view_file_content(file_path, safe_name)
        if error_message:
            return jsonify({"ok": False, "message": error_message}), 500

        return jsonify({"ok": True, "filename": safe_name, "content": text})

    # Route: /log-stream/<source>
    @app.route("/log-stream/<source>")
    def log_stream(source):
        """Runtime helper log_stream."""
        settings = state["_log_source_settings"](source)
        if settings is None:
            return Response("invalid log source", status=404)
        source_key = settings["source"]
        state["ensure_log_stream_fetcher_started"](source_key)
        db_topic = f"log:{source_key}"

        def generate():
            """Runtime helper generate."""
            state["_increment_log_stream_clients"](source_key)
            last_event_id = 0
            db_path = state.get("APP_STATE_DB_PATH")
            if db_path is not None:
                try:
                    latest_event = state_store_service.get_latest_event(db_path, topic=db_topic)
                except Exception:
                    latest_event = None
                if isinstance(latest_event, dict):
                    last_event_id = int(latest_event.get("id", 0) or 0)
            try:
                while True:
                    db_path = state.get("APP_STATE_DB_PATH")
                    if db_path is not None:
                        try:
                            rows = state_store_service.list_events_since(
                                db_path,
                                topic=db_topic,
                                since_id=last_event_id,
                                limit=120,
                            )
                        except Exception:
                            rows = []
                        if rows:
                            for row in rows:
                                payload = row.get("payload", {}) if isinstance(row, dict) else {}
                                line = str(payload.get("line", "") if isinstance(payload, dict) else "")
                                if line:
                                    yield f"data: {line}\n\n"
                                last_event_id = int(row.get("id", last_event_id) or last_event_id)
                            continue
                    yield ": keepalive\n\n"
                    time.sleep(state["LOG_STREAM_HEARTBEAT_SECONDS"])
            finally:
                state["_decrement_log_stream_clients"](source_key)

        return _sse_response(generate())

    # Route: /log-text/<source>
    @app.route("/log-text/<source>")
    def log_text(source):
        """Runtime helper log_text."""
        logs = state["get_log_source_text"](source)
        if logs is None:
            return jsonify({"logs": "(no logs)"}), 404
        return jsonify({"logs": logs})



