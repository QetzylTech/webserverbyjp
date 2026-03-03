"""Setup-mode helpers for initial env creation and validation."""

from __future__ import annotations

import getpass
import os
import secrets
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ENV_DEFAULTS = {
    "SERVICE": "minecraft",
    "DISPLAY_TZ": "Asia/Manila",
    "DOC_README_URL": "/doc/server_setup_doc.md",
    "MINECRAFT_ROOT_DIR": "/opt/Minecraft",
    "BACKUP_DIR": "/home/marites/backups",
    "BACKUP_INTERVAL_HOURS": "3",
    "BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS": "15",
    "BACKUP_WATCH_INTERVAL_OFF_SECONDS": "45",
    "IDLE_ZERO_PLAYERS_SECONDS": "180",
    "IDLE_CHECK_INTERVAL_SECONDS": "5",
    "IDLE_CHECK_INTERVAL_ACTIVE_SECONDS": "5",
    "IDLE_CHECK_INTERVAL_OFF_SECONDS": "15",
    "CRASH_STOP_GRACE_SECONDS": "15",
    "MC_QUERY_INTERVAL_SECONDS": "3",
    "METRICS_COLLECT_INTERVAL_SECONDS": "1",
    "METRICS_COLLECT_INTERVAL_OFF_SECONDS": "5",
    "METRICS_STREAM_HEARTBEAT_SECONDS": "5",
    "LOG_STREAM_HEARTBEAT_SECONDS": "5",
    "LOG_STREAM_EVENT_BUFFER_SIZE": "800",
    "MINECRAFT_LOG_TEXT_LIMIT": "1000",
    "MINECRAFT_JOURNAL_TAIL_LINES": "1000",
    "MINECRAFT_LOG_VISIBLE_LINES": "500",
    "BACKUP_LOG_TEXT_LIMIT": "200",
    "MCWEB_LOG_TEXT_LIMIT": "200",
    "MCWEB_ACTION_LOG_TEXT_LIMIT": "200",
    "HOME_PAGE_ACTIVE_TTL_SECONDS": "30",
    "HOME_PAGE_HEARTBEAT_INTERVAL_MS": "10000",
    "FILE_PAGE_CACHE_REFRESH_SECONDS": "15",
    "FILE_PAGE_ACTIVE_TTL_SECONDS": "30",
    "FILE_PAGE_HEARTBEAT_INTERVAL_MS": "10000",
    "SERVICE_STATUS_CACHE_ACTIVE_SECONDS": "1",
    "SERVICE_STATUS_CACHE_OFF_SECONDS": "5",
    "SLOW_METRICS_INTERVAL_ACTIVE_SECONDS": "1",
    "SLOW_METRICS_INTERVAL_OFF_SECONDS": "15",
    "LOG_FETCHER_IDLE_SLEEP_SECONDS": "2",
    "DEBUG": "false",
    "MAINTENANCE_SCOPE_BACKUP_ZIP": "true",
    "MAINTENANCE_SCOPE_STALE_WORLD_DIR": "true",
    "MAINTENANCE_SCOPE_OLD_WORLD_ZIP": "true",
    "MAINTENANCE_GUARD_NEVER_DELETE_NEWEST_N": "1",
    "MAINTENANCE_GUARD_NEVER_DELETE_LAST_BACKUP": "true",
    "MAINTENANCE_GUARD_PROTECT_ACTIVE_WORLD": "true",
}


_REQUIRED_KEYS = (
    "MCWEB_SECRET_KEY",
    "MCWEB_ADMIN_PASSWORD_HASH",
    "SERVICE",
    "DISPLAY_TZ",
    "MINECRAFT_ROOT_DIR",
    "BACKUP_DIR",
)


def assess_setup_requirement(config_path, values):
    """Return setup mode status based on env presence and required validity."""
    config_path = Path(config_path)
    reasons = []
    if not config_path.exists():
        reasons.append("mcweb.env not found.")
        return {"required": True, "reasons": reasons}

    raw = values if isinstance(values, dict) else {}
    for key in _REQUIRED_KEYS:
        if not str(raw.get(key, "")).strip():
            reasons.append(f"Missing required setting: {key}")

    tz_name = str(raw.get("DISPLAY_TZ", "")).strip()
    if tz_name:
        try:
            ZoneInfo(tz_name)
        except Exception:
            reasons.append(f"Invalid DISPLAY_TZ: {tz_name}")

    return {"required": bool(reasons), "reasons": reasons}


def setup_form_defaults(existing_values, app_dir):
    """Build setup form defaults from existing env values + app defaults."""
    _ = Path(app_dir)
    user_name = (
        str(os.environ.get("SUDO_USER", "")).strip()
        or str(os.environ.get("USER", "")).strip()
        or str(getpass.getuser() or "").strip()
    )
    if user_name:
        user_home = Path("/home") / user_name
    else:
        user_home = Path.home()
    base = dict(ENV_DEFAULTS)
    base["MINECRAFT_ROOT_DIR"] = str(user_home / "Minecraft")
    base["BACKUP_DIR"] = str(user_home / "backups")
    raw = existing_values if isinstance(existing_values, dict) else {}
    for key, value in raw.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            base[key] = text
    if not str(base.get("MCWEB_SECRET_KEY", "")).strip():
        base["MCWEB_SECRET_KEY"] = secrets.token_hex(32)
    return base


def write_env_file(config_path, values):
    """Write mcweb.env from validated setup values."""
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    payload = dict(ENV_DEFAULTS)
    for key in ("MCWEB_SECRET_KEY", "MCWEB_ADMIN_PASSWORD_HASH"):
        payload[key] = str(values.get(key, "")).strip()
    for key in (
        "SERVICE",
        "DISPLAY_TZ",
        "DOC_README_URL",
        "MINECRAFT_ROOT_DIR",
        "BACKUP_DIR",
    ):
        payload[key] = str(values.get(key, payload.get(key, ""))).strip()

    lines = ["# mcweb runtime config", ""]
    for key in sorted(payload.keys()):
        lines.append(f"{key}={payload[key]}")
    lines.append("")
    config_path.write_text("\n".join(lines), encoding="utf-8")


def validate_runtime_locations(values):
    """Validate setup runtime paths/service and return field-level errors."""
    errors = {}
    service_name = str(values.get("SERVICE", "")).strip()
    mc_root = Path(str(values.get("MINECRAFT_ROOT_DIR", "")).strip())
    backup_dir = Path(str(values.get("BACKUP_DIR", "")).strip())

    # Service must exist in systemd.
    try:
        probe = subprocess.run(
            ["systemctl", "show", service_name, "--property=LoadState", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        load_state = str(probe.stdout or "").strip().lower()
        if probe.returncode != 0 or load_state in {"", "not-found", "error"}:
            errors["SERVICE"] = "service not found."
    except Exception:
        errors["SERVICE"] = "service not found."

    root_result = validate_minecraft_root(str(mc_root), allow_create_missing=False)
    if root_result["errors"]:
        errors["MINECRAFT_ROOT_DIR"] = "\n".join(root_result["errors"])

    backup_result = validate_backup_location(str(backup_dir), allow_create_missing=False)
    if backup_result["errors"]:
        errors["BACKUP_DIR"] = "\n".join(backup_result["errors"])

    return errors


def validate_service_name(service_name):
    """Return service validation error string or empty string."""
    name = str(service_name or "").strip()
    if not name:
        return "service not found."
    try:
        probe = subprocess.run(
            ["systemctl", "show", name, "--property=LoadState", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        load_state = str(probe.stdout or "").strip().lower()
        if probe.returncode != 0 or load_state in {"", "not-found", "error"}:
            return "service not found."
        return ""
    except Exception:
        return "service not found."


def _existing_parent(path_obj):
    current = Path(path_obj)
    while True:
        if current.exists():
            return current
        if current.parent == current:
            return current
        current = current.parent


def _can_write_existing_dir(path_obj):
    probe_dir = Path(path_obj)
    if not probe_dir.exists() or not probe_dir.is_dir() or not os.access(str(probe_dir), os.W_OK):
        return False
    try:
        with tempfile.NamedTemporaryFile(dir=str(probe_dir), prefix=".mcweb_write_test_", delete=True):
            pass
        return True
    except Exception:
        return False


def _directory_state(path_value):
    path_obj = Path(str(path_value or "").strip())
    exists_dir = path_obj.exists() and path_obj.is_dir()
    if exists_dir:
        writable = _can_write_existing_dir(path_obj)
        return {
            "path": path_obj,
            "exists_dir": True,
            "missing": False,
            "writable": writable,
            "not_writable": not writable,
        }

    existing_parent = _existing_parent(path_obj.parent if path_obj.name else path_obj)
    parent_writable = _can_write_existing_dir(existing_parent) if existing_parent.exists() else False
    return {
        "path": path_obj,
        "exists_dir": False,
        "missing": True,
        "writable": False,
        "not_writable": not parent_writable,
    }


def validate_minecraft_root(path_value, allow_create_missing=False):
    """Return minecraft-root validation details."""
    state = _directory_state(path_value)
    errors = []
    if state["missing"]:
        errors.append("location does not exist.")
        if not allow_create_missing:
            errors.append("Enable 'Create folder' to continue.")
    if state["not_writable"]:
        errors.append("location not writable.")
    if state["exists_dir"]:
        props_path = state["path"] / "server.properties"
        if not props_path.exists() or not props_path.is_file():
            errors.append("no minecraft install found.")
    return {"errors": errors, "missing": state["missing"]}


def validate_backup_location(path_value, allow_create_missing=False):
    """Return backup-location validation details."""
    state = _directory_state(path_value)
    errors = []
    if state["missing"]:
        errors.append("location does not exist.")
        if not allow_create_missing:
            errors.append("Enable 'Create folder' to continue.")
    if state["not_writable"]:
        errors.append("backup location not writable.")
    return {"errors": errors, "missing": state["missing"]}


def archive_data_residuals(data_dir):
    """Move all residual data files/dirs into data/old_app_data."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    old_dir = data_dir / "old_app_data"
    old_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    for child in list(data_dir.iterdir()):
        if child.name == "old_app_data":
            continue
        target = old_dir / f"{child.name}.{stamp}"
        suffix = 1
        while target.exists():
            target = old_dir / f"{child.name}.{stamp}.{suffix}"
            suffix += 1
        child.replace(target)
