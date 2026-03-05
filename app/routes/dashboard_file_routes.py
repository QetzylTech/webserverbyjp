"""File/log/metrics route registration for the MC web dashboard."""
from collections import deque
import copy
import gzip
import json
from pathlib import Path
import threading
import tracemalloc
import time

from flask import Response, abort, after_this_request, jsonify, render_template, request, send_file, send_from_directory, stream_with_context
from app.core import profiling
from app.core import state_store as state_store_service
from app.ports import ports

_METRICS_ROUTE_CACHE_LOCK = threading.Lock()
_METRICS_ROUTE_CACHE_TTL_SECONDS = 1.0
_METRICS_ROUTE_CACHE = {
    "event_id": -1,
    "expires_at": 0.0,
    "payload": None,
}


def register_file_routes(app, state):
    """Register file browsing, log streaming, and metrics routes."""
    process_role = str(state.get("PROCESS_ROLE", "all") or "all").strip().lower()

    def _state_db_path():
        path = state.get("APP_STATE_DB_PATH")
        return path

    def _latest_metrics_from_db():
        db_path = _state_db_path()
        if db_path is None:
            return None, 0
        try:
            event = state_store_service.get_latest_event(db_path, topic="metrics_snapshot")
        except Exception:
            return None, 0
        if not isinstance(event, dict):
            return None, 0
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None, 0
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, dict):
            return None, 0
        return snapshot, int(event.get("id", 0) or 0)

    def _apply_operation_status_hint(payload):
        if not isinstance(payload, dict):
            return payload
        db_path = _state_db_path()
        if db_path is None:
            return payload
        try:
            rows = state_store_service.list_operations_by_status(
                db_path,
                statuses=("intent", "in_progress"),
                limit=20,
            )
        except Exception:
            return payload
        if not isinstance(rows, list) or not rows:
            return payload

        has_start = False
        has_stop = False
        has_restore = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            op_type = str(row.get("op_type", "") or "").strip().lower()
            if op_type == "start":
                has_start = True
            elif op_type == "stop":
                has_stop = True
            elif op_type == "restore":
                has_restore = True

        status_text = str(payload.get("service_status", "") or "").strip().lower()
        running_raw = str(payload.get("service_running_status", "") or "").strip().lower()
        is_off = status_text in {"off", ""} or running_raw in {"inactive", "failed", ""}
        if not is_off:
            return payload

        if has_restore or has_stop:
            patched = dict(payload)
            patched["service_status"] = "Shutting Down"
            patched["service_status_class"] = "stat-orange"
            patched["service_running_status"] = "shutting_down"
            return patched
        if has_start:
            patched = dict(payload)
            patched["service_status"] = "Starting"
            patched["service_status_class"] = "stat-yellow"
            patched["service_running_status"] = "starting"
            return patched
        return payload

    def _refresh_metrics_snapshot_best_effort():
        """Force a fresh metrics snapshot in web-only role."""
        if process_role != "web":
            return
        publish_fn = state.get("_collect_and_publish_metrics") or state.get("collect_and_publish_metrics")
        if not callable(publish_fn):
            return
        try:
            publish_fn()
        except Exception:
            pass

    def _snapshot_root_dir():
        return Path(getattr(state, "AUTO_SNAPSHOT_DIR", "") or (state["BACKUP_DIR"] / "snapshots"))

    def _resolve_snapshot_dir(snapshot_name):
        if not snapshot_name:
            return None, ""
        safe_name = Path(snapshot_name).name
        if safe_name != snapshot_name:
            return None, ""
        base_dir = _snapshot_root_dir()
        candidate = base_dir / safe_name
        try:
            base_resolved = base_dir.resolve()
            candidate_resolved = candidate.resolve()
            candidate_resolved.relative_to(base_resolved)
        except (OSError, ValueError):
            return None, ""
        if not candidate_resolved.exists() or not candidate_resolved.is_dir():
            return None, ""
        return candidate_resolved, safe_name

    def _log_file_source_spec(source):
        normalized = str(source or "").strip().lower()
        log_dir = state["MCWEB_LOG_FILE"].parent
        if normalized == "minecraft":
            return {
                "key": "minecraft",
                "base_dir": state["MINECRAFT_LOGS_DIR"],
                "patterns": ("*.log", "*.gz"),
                "download_base": "/download/minecraft-logs",
                "view_base": "/view-file/minecraft_logs",
            }
        if normalized == "backup":
            return {
                "key": "backup",
                "base_dir": log_dir,
                "patterns": ("backup.log*",),
                "download_base": "/download/log-files/backup",
                "view_base": "/view-log-file/backup",
            }
        if normalized == "mcweb":
            return {
                "key": "mcweb",
                "base_dir": log_dir,
                "patterns": ("mcweb_actions.log*",),
                "download_base": "/download/log-files/mcweb",
                "view_base": "/view-log-file/mcweb",
            }
        if normalized == "mcweb_log":
            return {
                "key": "mcweb_log",
                "base_dir": log_dir,
                "patterns": ("mcweb.log*",),
                "download_base": "/download/log-files/mcweb_log",
                "view_base": "/view-log-file/mcweb_log",
            }
        return None

    def _log_file_items_from_spec(spec):
        if not spec:
            return []
        merged_by_name = {}
        for pattern in spec["patterns"]:
            for item in state["_list_download_files"](spec["base_dir"], pattern, state["DISPLAY_TZ"]):
                merged_by_name[item["name"]] = dict(item)
        items = list(merged_by_name.values())
        items.sort(key=lambda item: item.get("mtime", 0), reverse=True)
        return items

    def _resolve_log_file(source, filename):
        spec = _log_file_source_spec(source)
        if spec is None:
            return None, None
        safe_name = state["_safe_filename_in_dir"](spec["base_dir"], filename)
        if safe_name is None:
            return spec, None
        return spec, safe_name

    # Route: /backups
    @app.route("/backups")
    def backups_page():
        """Runtime helper backups_page."""
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        return render_template(
            state["FILES_TEMPLATE_NAME"],
            current_page="backups",
            page_title="Backup & Restore",
            panel_title="Backup & Restore",
            panel_hint="Latest to oldest from backup zips and auto snapshots",
            items=state["get_cached_file_page_items"]("backups"),
            download_base="/download/backups",
            empty_text="No backups or snapshots found.",
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
            panel_hint=f"Latest to oldest from {state['CRASH_REPORTS_DIR']}",
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
            panel_hint=f"Latest to oldest from {state['MINECRAFT_LOGS_DIR']}",
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

    # Route: /download/backups-snapshot/<path:snapshot_name>
    @app.route("/download/backups-snapshot/<path:snapshot_name>", methods=["POST"])
    def download_snapshot(snapshot_name):
        """Zip one snapshot directory and download it as an attachment."""
        sudo_password = request.form.get("sudo_password", "")
        if not state["validate_sudo_password"](sudo_password):
            state["log_mcweb_action"]("download-snapshot", command=snapshot_name, rejection_message="Password incorrect.")
            return state["_password_rejected_response"]()
        state["record_successful_password_ip"]()

        snapshot_dir, safe_name = _resolve_snapshot_dir(snapshot_name)
        if snapshot_dir is None:
            state["log_mcweb_action"]("download-snapshot", command=snapshot_name, rejection_message="Snapshot not found or invalid path.")
            return abort(404)

        tracemalloc_started = False
        try:
            tmp_root = ports.filesystem.mkdtemp(prefix="mcweb_snapshot_zip_")
            if profiling.ENABLED and not tracemalloc.is_tracing():
                tracemalloc.start()
                tracemalloc_started = True
            started = time.perf_counter()
            zip_path = ports.filesystem.make_zip_archive(tmp_root / safe_name, root_dir=snapshot_dir)
            elapsed = time.perf_counter() - started
            profiling.record_duration("snapshot_download.zip_build", elapsed)
            if profiling.ENABLED and tracemalloc.is_tracing():
                _current, peak = tracemalloc.get_traced_memory()
                profiling.set_gauge("snapshot_download.zip_peak_bytes", int(peak))
                if tracemalloc_started:
                    tracemalloc.stop()

            @after_this_request
            def _cleanup_temp_zip(response):
                try:
                    ports.filesystem.rmtree(tmp_root, ignore_errors=True)
                except OSError:
                    pass
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
        finally:
            if tracemalloc_started and tracemalloc.is_tracing():
                tracemalloc.stop()

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
        spec, safe_name = _resolve_log_file(source, filename)
        if spec is None or safe_name is None:
            return abort(404)
        return send_from_directory(str(spec["base_dir"]), safe_name, as_attachment=True)

    # Route: /log-files/<source>
    @app.route("/log-files/<source>")
    def list_log_files(source):
        """Return file-list payload for one log source."""
        state["ensure_file_page_cache_refresher_started"]()
        state["_mark_file_page_client_active"]()
        spec = _log_file_source_spec(source)
        if spec is None:
            return jsonify({"ok": False, "message": "Invalid log file source."}), 404
        if spec["key"] == "minecraft":
            items = state["get_cached_file_page_items"]("minecraft_logs")
        else:
            items = _log_file_items_from_spec(spec)
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

    # Route: /view-log-file/<source>/<path:filename>
    @app.route("/view-log-file/<source>/<path:filename>")
    def view_log_file(source, filename):
        """View one non-minecraft log file by source key."""
        spec, safe_name = _resolve_log_file(source, filename)
        if spec is None:
            return jsonify({"ok": False, "message": "Invalid log file source."}), 404
        if safe_name is None:
            return jsonify({"ok": False, "message": "File not found."}), 404

        file_path = spec["base_dir"] / safe_name
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
        db_topic = f"log:{source_key}"

        def generate():
            """Runtime helper generate."""
            state["_increment_log_stream_clients"](source_key)
            last_event_id = 0
            db_path = _state_db_path()
            if db_path is not None:
                try:
                    latest_event = state_store_service.get_latest_event(db_path, topic=db_topic)
                except Exception:
                    latest_event = None
                if isinstance(latest_event, dict):
                    last_event_id = int(latest_event.get("id", 0) or 0)
            try:
                while True:
                    db_path = _state_db_path()
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
        now = time.time()
        _refresh_metrics_snapshot_best_effort()
        latest_snapshot = None
        latest_event_id = 0
        latest_snapshot, latest_event_id = _latest_metrics_from_db()
        with _METRICS_ROUTE_CACHE_LOCK:
            cached_payload = _METRICS_ROUTE_CACHE.get("payload")
            if (
                _METRICS_ROUTE_CACHE.get("event_id") == int(latest_event_id)
                and float(_METRICS_ROUTE_CACHE.get("expires_at", 0.0) or 0.0) >= now
                and isinstance(cached_payload, dict)
            ):
                return jsonify(copy.deepcopy(cached_payload))
        payload = latest_snapshot if isinstance(latest_snapshot, dict) else state["get_cached_dashboard_metrics"]()
        payload = _apply_operation_status_hint(payload)
        with _METRICS_ROUTE_CACHE_LOCK:
            _METRICS_ROUTE_CACHE["event_id"] = int(latest_event_id)
            _METRICS_ROUTE_CACHE["expires_at"] = now + _METRICS_ROUTE_CACHE_TTL_SECONDS
            _METRICS_ROUTE_CACHE["payload"] = copy.deepcopy(payload if isinstance(payload, dict) else {})
        return jsonify(payload)

    # Route: /metrics-stream
    @app.route("/metrics-stream")
    def metrics_stream():
        """Runtime helper metrics_stream."""
        def generate():
            """Runtime helper generate."""
            with state["metrics_cache_cond"]:
                state["metrics_stream_client_count"] += 1
                state["metrics_cache_cond"].notify_all()
            last_event_id = 0
            db_path = _state_db_path()
            if db_path is not None:
                try:
                    latest_event = state_store_service.get_latest_event(db_path, topic="metrics_snapshot")
                except Exception:
                    latest_event = None
                if isinstance(latest_event, dict):
                    latest_payload = latest_event.get("payload", {})
                    latest_snapshot = latest_payload.get("snapshot") if isinstance(latest_payload, dict) else None
                    last_event_id = int(latest_event.get("id", 0) or 0)
                    if isinstance(latest_snapshot, dict):
                        patched_snapshot = _apply_operation_status_hint(latest_snapshot)
                        payload = json.dumps(patched_snapshot, separators=(",", ":"))
                        yield f"data: {payload}\n\n"
            try:
                while True:
                    _refresh_metrics_snapshot_best_effort()
                    db_path = _state_db_path()
                    if db_path is not None:
                        try:
                            rows = state_store_service.list_events_since(
                                db_path,
                                topic="metrics_snapshot",
                                since_id=last_event_id,
                                limit=10,
                            )
                        except Exception:
                            rows = []
                        if rows:
                            for row in rows:
                                payload_obj = row.get("payload", {}) if isinstance(row, dict) else {}
                                snapshot = payload_obj.get("snapshot") if isinstance(payload_obj, dict) else None
                                if isinstance(snapshot, dict):
                                    patched_snapshot = _apply_operation_status_hint(snapshot)
                                    payload = json.dumps(patched_snapshot, separators=(",", ":"))
                                    yield f"data: {payload}\n\n"
                                last_event_id = int(row.get("id", last_event_id) or last_event_id)
                            continue
                    yield ": keepalive\n\n"
                    time.sleep(state["METRICS_STREAM_HEARTBEAT_SECONDS"])
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
