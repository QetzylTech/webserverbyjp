"""Command helpers for setup form validation and submission."""

from __future__ import annotations

from werkzeug.security import generate_password_hash

_REQUIRED_MESSAGE = "Please fill in all required fields."


def _to_bool(value):
    """Parse common truthy string variants from form payload values."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _submit_payload(form, defaults):
    """Normalize incoming setup form data into a canonical submitted dict."""
    fallback = defaults if isinstance(defaults, dict) else {}
    return {
        "SERVICE": str(form.get("service", fallback.get("SERVICE", "minecraft"))).strip() or "minecraft",
        "DISPLAY_TZ": str(form.get("display_tz", fallback.get("DISPLAY_TZ", ""))).strip(),
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
        if not submitted.get(key):
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
    defaults = existing_defaults if isinstance(existing_defaults, dict) else {}
    return {
        "MCWEB_ADMIN_PASSWORD_HASH": (
            generate_password_hash(password)
            if not is_paths_only
            else str(defaults.get("MCWEB_ADMIN_PASSWORD_HASH", "")).strip()
        ),
        "MCWEB_SECRET_KEY": str(defaults.get("MCWEB_SECRET_KEY", "")).strip(),
        "SERVICE": submitted["SERVICE"],
        "DISPLAY_TZ": submitted["DISPLAY_TZ"],
        "MINECRAFT_ROOT_DIR": submitted["MINECRAFT_ROOT_DIR"],
        "BACKUP_DIR": submitted["BACKUP_DIR"],
        "CREATE_BACKUP_DIR": submitted["CREATE_BACKUP_DIR"],
    }


def handle_setup_submit(form, defaults, *, is_paths_only, save_setup_values):
    """Validate setup form inputs, persist values, and return response payload."""
    submitted = _submit_payload(form, defaults)
    password = str(form.get("admin_password", "")).strip() if not is_paths_only else ""
    password_confirm = str(form.get("admin_password_confirm", "")).strip() if not is_paths_only else ""

    field_errors = _required_submit_errors(
        submitted,
        is_paths_only=is_paths_only,
        password=password,
        password_confirm=password_confirm,
    )
    if field_errors:
        return {"ok": False, "field_errors": field_errors, "message": _REQUIRED_MESSAGE}
    if not is_paths_only:
        password_field_errors, password_message = _password_errors(password, password_confirm)
        if password_field_errors:
            return {
                "ok": False,
                "field_errors": password_field_errors,
                "message": password_message,
            }

    values = _setup_values(
        submitted,
        defaults,
        is_paths_only=is_paths_only,
        password=password,
    )
    ok, message, service_field_errors = save_setup_values(values)
    if not ok:
        return {
            "ok": False,
            "field_errors": service_field_errors or {},
            "message": message or "Setup failed.",
        }
    return {"ok": True, "extra": {"redirect": "/"}}
