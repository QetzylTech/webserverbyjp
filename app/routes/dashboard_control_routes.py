"""Control action route registration for the MC web dashboard."""
import subprocess
import threading

from flask import jsonify, request


def register_control_routes(app, state, *, run_cleanup_event_if_enabled):
    """Register start/stop/backup/restore/RCON control routes."""

    # Route: /start
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

    # Route: /stop
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
        run_cleanup_event_if_enabled(state, "server_shutdown")
        state["log_mcweb_action"]("stop")
        return state["_ok_response"]()

    # Route: /backup
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

    # Route: /restore-backup
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

    # Route: /restore-status
    @app.route("/restore-status")
    def restore_status():
        """Runtime helper restore_status."""
        since = request.args.get("since", "0")
        job_id = (request.args.get("job_id", "") or "").strip() or None
        payload = state["get_restore_status"](since_seq=since, job_id=job_id)
        return jsonify(payload)

    # Route: /undo-restore
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

    # Route: /rcon
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
