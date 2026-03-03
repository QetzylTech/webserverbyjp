"""Setup-mode helpers for initial env creation and validation."""

from __future__ import annotations

import getpass
import os
import secrets
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.platform import get_paths
from app.services.setup_env_defaults import ENV_DEFAULTS, REQUIRED_KEYS
from app.services import setup_runtime_validation as _setup_validation

_paths = get_paths()
validate_runtime_locations = _setup_validation.validate_runtime_locations
validate_service_name = _setup_validation.validate_service_name
validate_minecraft_root = _setup_validation.validate_minecraft_root
validate_backup_location = _setup_validation.validate_backup_location


def assess_setup_requirement(config_path, values):
    """Return setup mode status based on env presence and required validity."""
    config_path = Path(config_path)
    reasons = []
    if not config_path.exists():
        reasons.append("mcweb.env not found.")
        return {"required": True, "reasons": reasons, "mode": "full"}

    raw = values if isinstance(values, dict) else {}
    path_keys = {"MINECRAFT_ROOT_DIR", "BACKUP_DIR"}
    blocking_non_path = []
    path_reasons = []
    for key in REQUIRED_KEYS:
        if not str(raw.get(key, "")).strip():
            message = f"Missing required setting: {key}"
            reasons.append(message)
            if key in path_keys:
                path_reasons.append(message)
            else:
                blocking_non_path.append(message)

    tz_name = str(raw.get("DISPLAY_TZ", "")).strip()
    if tz_name:
        try:
            ZoneInfo(tz_name)
        except Exception:
            message = f"Invalid DISPLAY_TZ: {tz_name}"
            reasons.append(message)
            blocking_non_path.append(message)

    for key in path_keys:
        text = str(raw.get(key, "")).strip()
        if text and not _paths.is_valid_env_path(text):
            message = f"Invalid {key} path format for detected OS."
            reasons.append(message)
            path_reasons.append(message)

    required = bool(reasons)
    mode = "paths_only" if required and not blocking_non_path and bool(path_reasons) else "full"
    return {"required": required, "reasons": reasons, "mode": mode}


def setup_form_defaults(existing_values):
    """Build setup form defaults from existing env values + app defaults."""
    user_name = (
        str(os.environ.get("SUDO_USER", "")).strip()
        or str(os.environ.get("USER", "")).strip()
        or str(getpass.getuser() or "").strip()
    )
    base = dict(ENV_DEFAULTS)
    base["MINECRAFT_ROOT_DIR"] = _paths.default_minecraft_root(user_name=user_name)
    base["BACKUP_DIR"] = _paths.default_backup_dir(user_name=user_name)
    raw = existing_values if isinstance(existing_values, dict) else {}
    for key, value in raw.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            if key in {"MINECRAFT_ROOT_DIR", "BACKUP_DIR"} and not _paths.is_valid_env_path(text):
                continue
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
