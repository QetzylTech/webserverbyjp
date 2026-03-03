"""Validation helpers used by setup form and save pipeline."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.platform import get_calls, get_paths

_calls = get_calls()
_paths = get_paths()


def validate_runtime_locations(values):
    """Validate setup runtime paths/service and return field-level errors."""
    errors = {}
    service_name = str(values.get("SERVICE", "")).strip()
    mc_root_text = str(values.get("MINECRAFT_ROOT_DIR", "")).strip()
    backup_dir_text = str(values.get("BACKUP_DIR", "")).strip()
    mc_root = Path(mc_root_text)
    backup_dir = Path(backup_dir_text)

    if not _is_service_loadable(service_name):
        errors["SERVICE"] = "service not found."

    root_result = validate_minecraft_root(str(mc_root))
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
    if not _is_service_loadable(name):
        return "service not found."
    return ""


def _is_service_loadable(service_name):
    """Probe OS service manager and return whether the named service is loadable."""
    try:
        probe = _calls.service_show_load_state(service_name, timeout=5)
        load_state = str(probe.stdout or "").strip().lower()
        return probe.returncode == 0 and load_state not in {"", "not-found", "error"}
    except Exception:
        return False


def _existing_parent(path_obj):
    """Walk up parents until an existing path is found."""
    current = Path(path_obj)
    while True:
        if current.exists():
            return current
        if current.parent == current:
            return current
        current = current.parent


def _can_write_existing_dir(path_obj):
    """Check effective write permission by creating a short-lived temp file."""
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
    """Return existence/writability state for a target directory path."""
    path_obj = Path(str(path_value or "").strip())
    exists_dir = path_obj.exists() and path_obj.is_dir()
    if exists_dir:
        writable = _can_write_existing_dir(path_obj)
        return {
            "path": path_obj,
            "exists_dir": True,
            "missing": False,
            "not_writable": not writable,
        }

    existing_parent = _existing_parent(path_obj.parent if path_obj.name else path_obj)
    parent_writable = _can_write_existing_dir(existing_parent) if existing_parent.exists() else False
    return {
        "path": path_obj,
        "exists_dir": False,
        "missing": True,
        "not_writable": not parent_writable,
    }


def validate_minecraft_root(path_value):
    """Return minecraft-root validation details."""
    if not _paths.is_valid_env_path(path_value):
        return {"errors": ["invalid path for detected OS."], "missing": False}
    state = _directory_state(path_value)
    errors = []
    if state["missing"]:
        errors.append("location does not exist.")
    if state["not_writable"]:
        errors.append("location not writable.")
    if state["exists_dir"]:
        props_path = state["path"] / "server.properties"
        if not props_path.exists() or not props_path.is_file():
            errors.append("no minecraft install found.")
    return {"errors": errors, "missing": state["missing"]}


def validate_backup_location(path_value, allow_create_missing=False):
    """Return backup-location validation details."""
    if not _paths.is_valid_env_path(path_value):
        return {"errors": ["invalid path for detected OS."], "missing": False}
    state = _directory_state(path_value)
    errors = []
    if state["missing"]:
        errors.append("location does not exist.")
        if not allow_create_missing:
            errors.append("Enable 'Create folder' to continue.")
    if state["not_writable"]:
        errors.append("backup location not writable.")
    return {"errors": errors, "missing": state["missing"]}
