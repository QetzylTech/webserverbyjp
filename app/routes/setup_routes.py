"""Setup page routes for first boot / invalid env scenarios."""

from __future__ import annotations

from datetime import datetime
from flask import abort, jsonify, render_template, request
from werkzeug.security import generate_password_hash
from zoneinfo import ZoneInfo, available_timezones
from app.services import setup_service as setup_service_service


def register_setup_routes(
    app,
    *,
    is_setup_required,
    setup_reasons,
    setup_defaults,
    save_setup_values,
):
    """Register setup page routes."""

    def _to_bool(value):
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @app.route("/setup", methods=["GET"])
    def setup_page():
        def _format_offset(total_minutes):
            sign = "+" if total_minutes >= 0 else "-"
            absolute = abs(int(total_minutes))
            hours = absolute // 60
            minutes = absolute % 60
            return f"UTC{sign}{hours:02d}:{minutes:02d}"

        def _timezone_options(default_tz):
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
            now_utc = datetime.utcnow()
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

        def _render(*, defaults=None, error_message="", field_errors=None, status_text=""):
            selected_defaults = defaults if isinstance(defaults, dict) else setup_defaults()
            return render_template(
                "setup.html",
                current_page="setup",
                reasons=setup_reasons(),
                defaults=selected_defaults,
                timezone_options=_timezone_options(selected_defaults.get("DISPLAY_TZ", "")),
                error_message=error_message,
                field_errors=field_errors or {},
                password_match_status=status_text,
            )

        if not is_setup_required():
            return abort(404)

        return _render(defaults=setup_defaults(), error_message="", field_errors={}, status_text="")

    @app.route("/setup/validate", methods=["POST"])
    def setup_validate():
        if not is_setup_required():
            return abort(404)
        payload = request.get_json(silent=True) or {}
        kind = str(payload.get("kind", "")).strip().lower()
        values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
        service_name = str(values.get("SERVICE", "minecraft")).strip() or "minecraft"

        if kind == "timezone":
            tz_name = str(values.get("DISPLAY_TZ", "")).strip()
            if not tz_name:
                return jsonify({"ok": False, "field_errors": {"DISPLAY_TZ": "This field is required."}, "message": "Please fill in all required fields."}), 400
            try:
                ZoneInfo(tz_name)
            except Exception:
                return jsonify({"ok": False, "field_errors": {"DISPLAY_TZ": "DISPLAY_TZ is invalid."}, "message": "DISPLAY_TZ is invalid."}), 400
            return jsonify({"ok": True})

        if kind == "root":
            root = str(values.get("MINECRAFT_ROOT_DIR", "")).strip()
            if not root:
                return jsonify({"ok": False, "field_errors": {"MINECRAFT_ROOT_DIR": "This field is required."}, "message": "Please fill in all required fields."}), 400
            service_error = setup_service_service.validate_service_name(service_name)
            root_result = setup_service_service.validate_minecraft_root(root, allow_create_missing=False)
            messages = []
            if service_error:
                messages.append(service_error)
            messages.extend(root_result["errors"])
            field_errors = {}
            if messages:
                field_errors["MINECRAFT_ROOT_DIR"] = "\n".join(messages)
            if field_errors:
                return jsonify(
                    {
                        "ok": False,
                        "field_errors": field_errors,
                        "message": field_errors.get("MINECRAFT_ROOT_DIR") or field_errors.get("SERVICE") or "Validation failed.",
                    }
                ), 400
            return jsonify({"ok": True})

        if kind == "backup":
            backup = str(values.get("BACKUP_DIR", "")).strip()
            if not backup:
                return jsonify({"ok": False, "field_errors": {"BACKUP_DIR": "This field is required."}, "message": "Please fill in all required fields."}), 400
            create_backup = _to_bool(values.get("CREATE_BACKUP_DIR"))
            backup_result = setup_service_service.validate_backup_location(backup, allow_create_missing=create_backup)
            if backup_result["errors"]:
                backup_error = "\n".join(backup_result["errors"])
                return jsonify(
                    {
                        "ok": False,
                        "field_errors": {"BACKUP_DIR": backup_error},
                        "missing_fields": {"BACKUP_DIR": bool(backup_result["missing"])},
                        "message": backup_error,
                    }
                ), 400
            return jsonify({"ok": True})

        return jsonify({"ok": False, "message": "Invalid setup validation request."}), 400

    @app.route("/setup/submit", methods=["POST"])
    def setup_submit():
        if not is_setup_required():
            return abort(404)

        form = request.form
        field_errors = {}
        submitted = {
            "SERVICE": str(form.get("service", "minecraft")).strip() or "minecraft",
            "DISPLAY_TZ": str(form.get("display_tz", "")).strip(),
            "MINECRAFT_ROOT_DIR": str(form.get("minecraft_root_dir", "")).strip(),
            "BACKUP_DIR": str(form.get("backup_dir", "")).strip(),
            "CREATE_BACKUP_DIR": _to_bool(form.get("create_backup_dir")),
        }
        password = str(form.get("admin_password", "")).strip()
        password_confirm = str(form.get("admin_password_confirm", "")).strip()

        for key in ("DISPLAY_TZ", "MINECRAFT_ROOT_DIR", "BACKUP_DIR"):
            if not submitted[key]:
                field_errors[key] = "This field is required."
        if not password:
            field_errors["ADMIN_PASSWORD"] = "This field is required."
        if not password_confirm:
            field_errors["ADMIN_PASSWORD_CONFIRM"] = "This field is required."
        if field_errors:
            return jsonify({"ok": False, "message": "Please fill in all required fields.", "field_errors": field_errors}), 400
        if len(password) < 8:
            return jsonify({"ok": False, "message": "Password must be at least 8 characters.", "field_errors": {"ADMIN_PASSWORD": "Password must be at least 8 characters."}}), 400
        if password != password_confirm:
            return jsonify({"ok": False, "message": "Passwords do not match.", "field_errors": {"ADMIN_PASSWORD": "Passwords do not match.", "ADMIN_PASSWORD_CONFIRM": "Passwords do not match."}}), 400

        values = {
            "MCWEB_ADMIN_PASSWORD_HASH": generate_password_hash(password),
            "SERVICE": submitted["SERVICE"],
            "DISPLAY_TZ": submitted["DISPLAY_TZ"],
            "MINECRAFT_ROOT_DIR": submitted["MINECRAFT_ROOT_DIR"],
            "BACKUP_DIR": submitted["BACKUP_DIR"],
            "CREATE_BACKUP_DIR": submitted["CREATE_BACKUP_DIR"],
        }
        ok, message, service_field_errors = save_setup_values(values)
        if not ok:
            return jsonify({"ok": False, "message": message or "Setup failed.", "field_errors": service_field_errors or {}}), 400
        return jsonify({"ok": True, "redirect": "/"})
