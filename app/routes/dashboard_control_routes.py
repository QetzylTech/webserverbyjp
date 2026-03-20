"""HTTP translation layer for control routes."""
import threading
# mypy: disable-error-code=untyped-decorator
from typing import Any, cast

from flask import jsonify, request

from app.commands import control_commands

_control_commands = cast(Any, control_commands)


def register_control_routes(app: Any, state: dict[str, Any], *, run_cleanup_event_if_enabled: Any) -> None:
    """Register start/stop/backup/restore/RCON routes via command handlers."""
    process_role = str(state.get("PROCESS_ROLE", "all") or "all").strip().lower()
    ctx = _control_commands.ControlCommandContext(
        state=state,
        process_role=process_role,
        run_cleanup_event_if_enabled=run_cleanup_event_if_enabled,
        threading_module=threading,
    )

    def _client_key() -> str:
        getter = state.get("_get_client_ip")
        if callable(getter):
            try:
                return str(getter() or "unknown")
            except Exception:
                pass
        xff = (request.headers.get("X-Forwarded-For", "") or "").strip()
        if xff:
            return xff.split(",")[0].strip()
        return str(request.remote_addr or "unknown")

    def _idempotency_key() -> str:
        header_value = (request.headers.get("X-Idempotency-Key", "") or "").strip()
        form_value = (request.form.get("idempotency_key", "") or "").strip()
        return header_value or form_value

    def _finalize(result: Any) -> Any:
        if result.response is not None:
            return result.response
        payload = result.payload if result.payload is not None else {}
        response = jsonify(payload)
        response.status_code = int(result.status_code or 200)
        if result.headers:
            response.headers.update(result.headers)
        return response

    @app.route("/start", methods=["POST"])
    def start() -> Any:
        result = _control_commands.start_operation(
            ctx,
            idempotency_key=_idempotency_key(),
            client_key=_client_key(),
        )
        return _finalize(result)

    @app.route("/stop", methods=["POST"])
    def stop() -> Any:
        result = _control_commands.stop_operation(
            ctx,
            idempotency_key=_idempotency_key(),
            client_key=_client_key(),
            sudo_password=request.form.get("sudo_password", ""),
        )
        return _finalize(result)

    @app.route("/backup", methods=["POST"])
    def backup() -> Any:
        result = _control_commands.backup_operation(
            ctx,
            idempotency_key=_idempotency_key(),
            client_key=_client_key(),
        )
        return _finalize(result)

    @app.route("/restore-backup", methods=["POST"])
    def restore_backup() -> Any:
        result = _control_commands.restore_operation(
            ctx,
            idempotency_key=_idempotency_key(),
            client_key=_client_key(),
            sudo_password=request.form.get("sudo_password", ""),
            filename=(request.form.get("filename", "") or "").strip(),
        )
        return _finalize(result)

    @app.route("/restore-status")
    def restore_status() -> Any:
        result = _control_commands.restore_status(
            ctx,
            since=request.args.get("since", "0"),
            job_id=(request.args.get("job_id", "") or "").strip() or None,
        )
        return _finalize(result)

    @app.route("/stream/restore_logs")
    def restore_logs_stream() -> Any:
        since = request.args.get("since", "") or request.headers.get("Last-Event-ID", "") or "0"
        result = _control_commands.restore_log_stream(
            ctx,
            since=since,
            job_id=(request.args.get("job_id", "") or "").strip() or None,
        )
        return _finalize(result)

    @app.route("/operation-status/<op_id>")
    def operation_status(op_id: str) -> Any:
        result = _control_commands.operation_status(
            ctx,
            op_id=op_id,
            client_key=_client_key(),
        )
        return _finalize(result)

    @app.route("/rcon", methods=["POST"])
    def rcon() -> Any:
        result = _control_commands.rcon_command(
            ctx,
            client_key=_client_key(),
            command=request.form.get("rcon_command", "").strip(),
            sudo_password=request.form.get("sudo_password", ""),
        )
        return _finalize(result)
