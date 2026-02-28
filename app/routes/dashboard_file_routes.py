"""File/log/metrics route registration for the MC web dashboard."""
from collections import deque
import gzip
import json

from flask import Response, abort, jsonify, render_template, request, send_from_directory, stream_with_context


def register_file_routes(app, state):
    """Register file browsing, log streaming, and metrics routes."""

    # Route: /backups
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

    # Route: /crash-logs
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

    # Route: /minecraft-logs
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

    # Route: /file-page-heartbeat
    @app.route("/file-page-heartbeat", methods=["POST"])
    def file_page_heartbeat():
        """Runtime helper file_page_heartbeat."""
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        return ("", 204)

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
        max_bytes = 2_000_000
        try:
            if safe_name.lower().endswith(".gz"):
                # Stream-decompress and keep only a tail window for very large gzip logs.
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

    # Route: /log-stream/<source>
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

    # Route: /log-text/<source>
    @app.route("/log-text/<source>")
    def log_text(source):
        """Runtime helper log_text."""
        logs = state["get_log_source_text"](source)
        if logs is None:
            return jsonify({"logs": "(no logs)"}), 404
        return jsonify({"logs": logs})

    # Route: /metrics
    @app.route("/metrics")
    def metrics():
        """Runtime helper metrics."""
        return jsonify(state["get_cached_dashboard_metrics"]())

    # Route: /metrics-stream
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
