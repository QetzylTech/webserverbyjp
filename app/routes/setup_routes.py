"""Setup page routes for first boot / invalid env scenarios."""

from __future__ import annotations

from flask import abort, jsonify, render_template, request
from app.commands import setup_commands
from app.queries import setup_queries
from app.services import setup_service as setup_service_service

_REQUIRED_MESSAGE = "Please fill in all required fields."


def register_setup_routes(
    app,
    *,
    is_setup_required,
    setup_mode,
    setup_defaults,
    save_setup_values,
):
    """Register setup page routes."""

    def _json_fail(field_errors=None, message="", status=400, extra=None):
        payload = {
            "ok": False,
            "message": message or _REQUIRED_MESSAGE,
            "field_errors": field_errors or {},
        }
        if isinstance(extra, dict):
            payload.update(extra)
        return jsonify(payload), status

    def _json_ok(extra=None):
        payload = {"ok": True}
        if isinstance(extra, dict):
            payload.update(extra)
        return jsonify(payload)

    def _is_paths_only_mode():
        return str(setup_mode() or "full").strip().lower() == "paths_only"

    def _ensure_setup_required():
        return None

    @app.route("/setup", methods=["GET"])
    def setup_page():
        _ensure_setup_required()
        selected_defaults = setup_defaults()
        return render_template(
            "setup.html",
            current_page="setup",
            defaults=selected_defaults,
            timezone_options=setup_queries.build_timezone_options(selected_defaults.get("DISPLAY_TZ", "")),
            error_message="",
            field_errors={},
            path_only_mode=_is_paths_only_mode(),
        )

    @app.route("/setup/validate", methods=["POST"])
    def setup_validate():
        _ensure_setup_required()
        payload = request.get_json(silent=True) or {}
        kind = str(payload.get("kind", "")).strip().lower()
        values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
        result = setup_queries.validate_setup_request(kind, values)
        if result.get("ok"):
            return _json_ok(result.get("extra"))
        return _json_fail(
            field_errors=result.get("field_errors"),
            message=result.get("message", ""),
            extra=result.get("extra"),
        )

    @app.route("/setup/submit", methods=["POST"])
    def setup_submit():
        _ensure_setup_required()
        result = setup_commands.handle_setup_submit(
            request.form,
            setup_defaults(),
            is_paths_only=_is_paths_only_mode(),
            save_setup_values=save_setup_values,
        )
        if result.get("ok"):
            return _json_ok(result.get("extra"))
        return _json_fail(
            field_errors=result.get("field_errors"),
            message=result.get("message", ""),
        )
