"""Setup page routes for first boot / invalid env scenarios."""

from __future__ import annotations

from datetime import datetime, timezone
from flask import abort, jsonify, render_template, request
from werkzeug.security import generate_password_hash
from zoneinfo import ZoneInfo, available_timezones
from app.services import setup_service as setup_service_service

_REQUIRED_MESSAGE = "Please fill in all required fields."


def _format_offset(total_minutes):
    """Render timezone offset minutes as a stable UTC+HH:MM label."""
    sign = "+" if total_minutes >= 0 else "-"
    absolute = abs(int(total_minutes))
    hours = absolute // 60
    minutes = absolute % 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _timezone_options(default_tz):
    """Build timezone dropdown options sorted by numeric UTC offset then name."""
    fallback = [
        "UTC",
        "Asia/Manila",
        "Asia/Singapore",
        "Asia/Tokyo",
        "Europe/London",
        "America/New_York",
        "America/Los_Angeles",
    ]
    try:
        zones = sorted(available_timezones())
    except Exception:
        zones = list(fallback)
    selected = str(default_tz or "").strip()
    if selected and selected not in zones:
        zones.append(selected)
    items = []
    now_utc = datetime.now(timezone.utc)
    for tz_name in zones:
        try:
            offset = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz_name)).utcoffset()
            minutes = int(offset.total_seconds() // 60) if offset is not None else 0
        except Exception:
            minutes = 0
        items.append(
            {
                "value": tz_name,
                "offset_minutes": minutes,
                "label": f"{_format_offset(minutes)} - {tz_name}",
            }
        )
    items.sort(key=lambda item: (item["offset_minutes"], item["value"]))
    return items


def _to_bool(value):
    """Parse common truthy string variants from form payload values."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _submit_payload(form, defaults):
    """Normalize incoming setup form data into a canonical submitted dict."""
    return {
        "SERVICE": str(form.get("service", defaults.get("SERVICE", "minecraft"))).strip() or "minecraft",
        "DISPLAY_TZ": str(form.get("display_tz", defaults.get("DISPLAY_TZ", ""))).strip(),
        "MINECRAFT_ROOT_DIR": str(form.get("minecraft_root_dir", "")).strip(),
        "BACKUP_DIR": str(form.get("backup_dir", "")).strip(),
        "CREATE_BACKUP_DIR": _to_bool(form.get("create_backup_dir")),
    }


def _required_submit_errors(submitted, *, is_paths_only, password, password_confirm):
    """Return field errors for required setup inputs based on active setup mode."""
    required_keys = (
        ("MINECRAFT_ROOT_DIR", "BACKUP_DIR")
        if is_paths_only
        else ("DISPLAY_TZ", "MINECRAFT_ROOT_DIR", "BACKUP_DIR")
    )
    errors = {}
    for key in required_keys:
        if not submitted[key]:
            errors[key] = "This field is required."
    if not is_paths_only:
        if not password:
            errors["ADMIN_PASSWORD"] = "This field is required."
        if not password_confirm:
            errors["ADMIN_PASSWORD_CONFIRM"] = "This field is required."
    return errors


def _password_errors(password, password_confirm):
    """Return password validation errors and a message when invalid."""
    if len(password) < 8:
        return {"ADMIN_PASSWORD": "Password must be at least 8 characters."}, "Password must be at least 8 characters."
    if password != password_confirm:
        return {
            "ADMIN_PASSWORD": "Passwords do not match.",
            "ADMIN_PASSWORD_CONFIRM": "Passwords do not match.",
        }, "Passwords do not match."
    return {}, ""


def _setup_values(submitted, existing_defaults, *, is_paths_only, password):
    """Build persisted setup values including hashed password and secret reuse."""
    return {
        "MCWEB_ADMIN_PASSWORD_HASH": (
            generate_password_hash(password)
            if not is_paths_only
            else str(existing_defaults.get("MCWEB_ADMIN_PASSWORD_HASH", "")).strip()
        ),
        "MCWEB_SECRET_KEY": str(existing_defaults.get("MCWEB_SECRET_KEY", "")).strip(),
        "SERVICE": submitted["SERVICE"],
        "DISPLAY_TZ": submitted["DISPLAY_TZ"],
        "MINECRAFT_ROOT_DIR": submitted["MINECRAFT_ROOT_DIR"],
        "BACKUP_DIR": submitted["BACKUP_DIR"],
        "CREATE_BACKUP_DIR": submitted["CREATE_BACKUP_DIR"],
    }


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

    def _required_field_error(field_key):
        return _json_fail(
            field_errors={field_key: "This field is required."},
            message=_REQUIRED_MESSAGE,
        )

    def _is_paths_only_mode():
        return str(setup_mode() or "full").strip().lower() == "paths_only"

    def _ensure_setup_required():
        if not is_setup_required():
            abort(404)

    @app.route("/setup", methods=["GET"])
    def setup_page():
        _ensure_setup_required()
        selected_defaults = setup_defaults()
        return render_template(
            "setup.html",
            current_page="setup",
            defaults=selected_defaults,
            timezone_options=_timezone_options(selected_defaults.get("DISPLAY_TZ", "")),
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
        service_name = str(values.get("SERVICE", "minecraft")).strip() or "minecraft"

        if kind == "timezone":
            tz_name = str(values.get("DISPLAY_TZ", "")).strip()
            if not tz_name:
                return _required_field_error("DISPLAY_TZ")
            try:
                ZoneInfo(tz_name)
            except Exception:
                return _json_fail(field_errors={"DISPLAY_TZ": "DISPLAY_TZ is invalid."}, message="DISPLAY_TZ is invalid.")
            return _json_ok()

        if kind == "root":
            root = str(values.get("MINECRAFT_ROOT_DIR", "")).strip()
            if not root:
                return _required_field_error("MINECRAFT_ROOT_DIR")
            service_error = setup_service_service.validate_service_name(service_name)
            root_result = setup_service_service.validate_minecraft_root(root)
            messages = []
            if service_error:
                messages.append(service_error)
            messages.extend(root_result["errors"])
            field_errors = {}
            if messages:
                field_errors["MINECRAFT_ROOT_DIR"] = "\n".join(messages)
            if field_errors:
                return _json_fail(
                    field_errors=field_errors,
                    message=field_errors.get("MINECRAFT_ROOT_DIR") or field_errors.get("SERVICE") or "Validation failed.",
                )
            return _json_ok()

        if kind == "backup":
            backup = str(values.get("BACKUP_DIR", "")).strip()
            if not backup:
                return _required_field_error("BACKUP_DIR")
            create_backup = _to_bool(values.get("CREATE_BACKUP_DIR"))
            backup_result = setup_service_service.validate_backup_location(backup, allow_create_missing=create_backup)
            if backup_result["errors"]:
                backup_error = "\n".join(backup_result["errors"])
                return _json_fail(
                    field_errors={"BACKUP_DIR": backup_error},
                    message=backup_error,
                    extra={"missing_fields": {"BACKUP_DIR": bool(backup_result["missing"])}},
                )
            return _json_ok()

        return _json_fail(message="Invalid setup validation request.")

    @app.route("/setup/submit", methods=["POST"])
    def setup_submit():
        _ensure_setup_required()
        is_paths_only = _is_paths_only_mode()

        form = request.form
        existing_defaults = setup_defaults()
        submitted = _submit_payload(form, existing_defaults)
        password = str(form.get("admin_password", "")).strip() if not is_paths_only else ""
        password_confirm = str(form.get("admin_password_confirm", "")).strip() if not is_paths_only else ""

        field_errors = _required_submit_errors(
            submitted,
            is_paths_only=is_paths_only,
            password=password,
            password_confirm=password_confirm,
        )
        if field_errors:
            return _json_fail(field_errors=field_errors, message=_REQUIRED_MESSAGE)
        if not is_paths_only:
            password_field_errors, password_message = _password_errors(password, password_confirm)
            if password_field_errors:
                return _json_fail(field_errors=password_field_errors, message=password_message)

        values = _setup_values(
            submitted,
            existing_defaults,
            is_paths_only=is_paths_only,
            password=password,
        )
        ok, message, service_field_errors = save_setup_values(values)
        if not ok:
            return _json_fail(field_errors=service_field_errors or {}, message=message or "Setup failed.")
        return _json_ok({"redirect": "/"})
