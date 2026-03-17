"""Read-side setup helpers for validation and option lists."""

from __future__ import annotations

from datetime import datetime, timezone
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


def _to_bool(value):
    """Parse common truthy string variants from form payload values."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def build_timezone_options(default_tz):
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


def _required_field_error(field_key):
    return {
        "ok": False,
        "message": _REQUIRED_MESSAGE,
        "field_errors": {field_key: "This field is required."},
    }


def validate_setup_request(kind, values):
    """Validate setup request payload and return an ok/err response payload."""
    kind = str(kind or "").strip().lower()
    values = values if isinstance(values, dict) else {}
    service_name = str(values.get("SERVICE", "minecraft")).strip() or "minecraft"

    if kind == "timezone":
        tz_name = str(values.get("DISPLAY_TZ", "")).strip()
        if not tz_name:
            return _required_field_error("DISPLAY_TZ")
        try:
            ZoneInfo(tz_name)
        except Exception:
            return {
                "ok": False,
                "message": "DISPLAY_TZ is invalid.",
                "field_errors": {"DISPLAY_TZ": "DISPLAY_TZ is invalid."},
            }
        return {"ok": True}

    if kind == "root":
        root = str(values.get("MINECRAFT_ROOT_DIR", "")).strip()
        if not root:
            return _required_field_error("MINECRAFT_ROOT_DIR")
        service_error = setup_service_service.validate_service_name(
            service_name,
            minecraft_root=root,
        )
        root_result = setup_service_service.validate_minecraft_root(root)
        messages = []
        if service_error:
            messages.append(service_error)
        messages.extend(root_result.get("errors", []))
        if messages:
            field_errors = {"MINECRAFT_ROOT_DIR": "\n".join(messages)}
            return {
                "ok": False,
                "message": field_errors.get("MINECRAFT_ROOT_DIR") or "Validation failed.",
                "field_errors": field_errors,
            }
        return {"ok": True}

    if kind == "backup":
        backup = str(values.get("BACKUP_DIR", "")).strip()
        if not backup:
            return _required_field_error("BACKUP_DIR")
        create_backup = _to_bool(values.get("CREATE_BACKUP_DIR"))
        backup_result = setup_service_service.validate_backup_location(backup, allow_create_missing=create_backup)
        if backup_result.get("errors"):
            backup_error = "\n".join(backup_result["errors"])
            return {
                "ok": False,
                "message": backup_error,
                "field_errors": {"BACKUP_DIR": backup_error},
                "extra": {"missing_fields": {"BACKUP_DIR": bool(backup_result.get("missing"))}},
            }
        return {"ok": True}

    return {"ok": False, "message": "Invalid setup validation request."}
