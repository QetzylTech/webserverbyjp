"""Setup page routes for first boot / invalid env scenarios."""

from __future__ import annotations
# mypy: disable-error-code=untyped-decorator

from collections.abc import Callable, Mapping
from typing import Any

from flask import jsonify, render_template, request
from app.commands import setup_commands
from app.queries import setup_queries
from app.services import setup_service as setup_service_service

_REQUIRED_MESSAGE = "Please fill in all required fields."


def register_setup_routes(
    app: Any,
    *,
    is_setup_required: Callable[[], bool],
    setup_mode: Callable[[], str],
    setup_defaults: Callable[[], Mapping[str, object]],
    save_setup_values: Callable[[dict[str, object]], tuple[bool, str, dict[str, str]]],
) -> None:
    """Register setup page routes."""

    def _json_fail(
        field_errors: Mapping[str, str] | None = None,
        message: str = "",
        status: int = 400,
        extra: Mapping[str, object] | None = None,
    ) -> Any:
        payload = {
            "ok": False,
            "message": message or _REQUIRED_MESSAGE,
            "field_errors": field_errors or {},
        }
        if isinstance(extra, dict):
            payload.update(extra)
        return jsonify(payload), status

    def _json_ok(extra: Mapping[str, object] | None = None) -> Any:
        payload = {"ok": True}
        if isinstance(extra, dict):
            payload.update(extra)
        return jsonify(payload)

    def _is_paths_only_mode() -> bool:
        return str(setup_mode() or "full").strip().lower() == "paths_only"

    def _ensure_setup_required() -> None:
        return None

    @app.route("/setup", methods=["GET"])
    def setup_page() -> Any:
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
    def setup_validate() -> Any:
        _ensure_setup_required()
        payload = request.get_json(silent=True) or {}
        kind = str(payload.get("kind", "")).strip().lower()
        values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
        result = setup_queries.validate_setup_request(kind, values)
        if result.get("ok"):
            extra = result.get("extra")
            return _json_ok(extra if isinstance(extra, Mapping) else None)
        field_errors = result.get("field_errors")
        message = result.get("message")
        extra = result.get("extra")
        return _json_fail(
            field_errors=field_errors if isinstance(field_errors, Mapping) else None,
            message=str(message or ""),
            extra=extra if isinstance(extra, Mapping) else None,
        )

    @app.route("/setup/submit", methods=["POST"])
    def setup_submit() -> Any:
        _ensure_setup_required()
        result = setup_commands.handle_setup_submit(
            request.form,
            setup_defaults(),
            is_paths_only=_is_paths_only_mode(),
            save_setup_values=save_setup_values,
        )
        if result.get("ok"):
            extra = result.get("extra")
            return _json_ok(extra if isinstance(extra, Mapping) else None)
        field_errors = result.get("field_errors")
        message = result.get("message")
        return _json_fail(
            field_errors=field_errors if isinstance(field_errors, Mapping) else None,
            message=str(message or ""),
        )
