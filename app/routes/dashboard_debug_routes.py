"""Debug route registration for the MC web dashboard."""
from flask import abort, jsonify, redirect, render_template, request


def register_debug_routes(app, state, *, is_maintenance_allowed, dummy_debug_env_rows):
    """Register debug page routes."""

    # Route: /debug
    @app.route("/debug")
    def debug_page():
        """Runtime helper debug_page."""
        if not state["DEBUG_PAGE_VISIBLE"]:
            return abort(404)
        if state["DEV_ENABLED"] and not is_maintenance_allowed(state):
            return abort(404)
        debug_message = (request.args.get("msg", "") or "").strip()
        debug_actions_enabled = bool(state["DEBUG_ENABLED"])
        props = state["get_debug_server_properties_rows"]() if debug_actions_enabled else {}
        editor_path = props.get("path", "server.properties")
        debug_rows = state["get_debug_env_rows"]() if debug_actions_enabled else dummy_debug_env_rows()
        return render_template(
            "debug.html",
            current_page="debug",
            debug_rows=debug_rows,
            csrf_token=state["_ensure_csrf_token"](),
            debug_message=debug_message,
            debug_server_properties_path=editor_path,
            debug_actions_enabled=debug_actions_enabled,
        )

    # Route: /debug/server-properties
    @app.route("/debug/server-properties")
    def debug_server_properties_get():
        """Runtime helper debug_server_properties_get."""
        if not state["DEBUG_ENABLED"]:
            return abort(404)
        payload = state["get_debug_server_properties_rows"]()
        status = 200 if payload.get("ok") else 500
        return jsonify(payload), status

    # Route: /debug/server-properties
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

    # Route: /debug/env
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

    # Route: /debug/explorer/list
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

    # Route: /debug/start
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

    # Route: /debug/backup
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

    # Route: /debug/stop
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
