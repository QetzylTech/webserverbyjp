from __future__ import annotations

from pathlib import Path
import secrets
from zoneinfo import ZoneInfo


def _to_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def save_setup_values(
    values,
    *,
    setup_service,
    data_bootstrap_service,
    web_conf_path,
    data_dir,
    app_state_db_path,
    setup_required_state,
    trigger_process_reload,
    log_mcweb_log,
    log_mcweb_exception,
):
    for key in ("SERVICE", "DISPLAY_TZ", "MINECRAFT_ROOT_DIR", "BACKUP_DIR"):
        if not str(values.get(key, "")).strip():
            return False, f"{key} is required.", {key: "This field is required."}
    try:
        ZoneInfo(str(values.get("DISPLAY_TZ", "")).strip())
    except Exception:
        return False, "DISPLAY_TZ is invalid.", {"DISPLAY_TZ": "DISPLAY_TZ is invalid."}
    try:
        if _to_bool(values.get("CREATE_BACKUP_DIR")):
            Path(str(values.get("BACKUP_DIR", "")).strip()).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    runtime_errors = setup_service.validate_runtime_locations(values)
    if runtime_errors:
        if "SERVICE" in runtime_errors:
            return False, "service not found.", runtime_errors
        if "MINECRAFT_ROOT_DIR" in runtime_errors:
            return False, runtime_errors["MINECRAFT_ROOT_DIR"], runtime_errors
        if "BACKUP_DIR" in runtime_errors:
            return False, runtime_errors["BACKUP_DIR"], runtime_errors
        return False, "Setup validation failed.", runtime_errors
    try:
        normalized = dict(values)
        normalized["MCWEB_SECRET_KEY"] = secrets.token_hex(32)
        setup_service.write_env_file(web_conf_path, normalized)
        setup_service.archive_data_residuals(data_dir)
        data_bootstrap_service.ensure_data_bootstrap(
            data_dir=data_dir,
            app_state_db_path=app_state_db_path,
            log_mcweb_log=log_mcweb_log,
            log_mcweb_exception=log_mcweb_exception,
        )
        setup_required_state["required"] = False
        setup_required_state["reasons"] = []
        trigger_process_reload()
        return True, "", {}
    except Exception as exc:
        log_mcweb_exception("setup/save", exc)
        return False, "Failed to save setup values.", {}
