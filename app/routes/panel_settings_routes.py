"""Panel settings routes for admin configuration."""

from __future__ import annotations
# mypy: disable-error-code=untyped-decorator

import csv
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from flask import jsonify, render_template, request
from werkzeug.security import check_password_hash, generate_password_hash

from app.core import state_store as state_store_service
from app.routes.shell_page import render_shell_page as render_shell_page_helper
from app.services import setup_service as setup_service_service
from app.queries import setup_queries as setup_queries_service
from app.services.worker_scheduler import start_detached

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _state_value(state: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        if hasattr(state, "get"):
            value = state.get(key)
        else:
            value = getattr(state, key, None)
    except Exception:
        value = None
    return default if value is None else value


def _to_bool(value: object) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _json_ok(extra: Mapping[str, Any] | None = None, status: int = 200) -> Any:
    payload = {"ok": True}
    if isinstance(extra, dict):
        payload.update(extra)
    return jsonify(payload), status


def _json_fail(
    message: str,
    *,
    status: int = 400,
    field_errors: Mapping[str, str] | None = None,
    extra: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> Any:
    payload = {"ok": False, "message": message}
    if error:
        payload["error"] = error
    if isinstance(field_errors, dict):
        payload["field_errors"] = field_errors
    if isinstance(extra, dict):
        payload.update(extra)
    return jsonify(payload), status


def _panel_app_dir(state: Mapping[str, Any]) -> Path:
    docs_dir = _state_value(state, "DOCS_DIR")
    if docs_dir:
        return Path(docs_dir).parent
    return Path(__file__).resolve().parents[2]


def _panel_web_conf_path(state: Mapping[str, Any]) -> Path:
    return _panel_app_dir(state) / "mcweb.env"


def _web_cfg_values(state: Mapping[str, Any]) -> dict[str, str]:
    raw_values = _state_value(state, "WEB_CFG_VALUES", {})
    if isinstance(raw_values, dict):
        return raw_values
    if isinstance(raw_values, Mapping):
        return {str(key): str(value) for key, value in raw_values.items()}
    return {}


def _load_env_defaults(state: Mapping[str, Any]) -> tuple[dict[str, str], Path, Path]:
    app_dir = _panel_app_dir(state)
    web_conf_path = _panel_web_conf_path(state)
    raw_values = _web_cfg_values(state)
    defaults = setup_service_service.setup_form_defaults(raw_values)
    defaults["MCWEB_REQUIRE_PASSWORD"] = "true" if _to_bool(defaults.get("MCWEB_REQUIRE_PASSWORD", "true")) else "false"
    admin_hash = str(defaults.get("MCWEB_ADMIN_PASSWORD_HASH", "") or "").strip()
    superadmin_hash = str(raw_values.get("MCWEB_SUPERADMIN_PASSWORD_HASH", admin_hash) or "").strip()
    if not superadmin_hash and admin_hash:
        superadmin_hash = admin_hash
    defaults["MCWEB_SUPERADMIN_PASSWORD_HASH"] = superadmin_hash
    if admin_hash and str(raw_values.get("MCWEB_SUPERADMIN_PASSWORD_HASH", "") or "").strip() != superadmin_hash:
        _save_env_values(state, defaults, web_conf_path)
    return defaults, web_conf_path, app_dir


def _trigger_process_reload(state: Mapping[str, Any]) -> None:
    def _reload() -> None:
        time.sleep(0.35)
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as exc:
            try:
                state["log_mcweb_exception"]("panel_settings/reload", exc)
            except Exception:
                pass

    start_detached(target=_reload, daemon=True)


def _validate_admin_password(state: Mapping[str, Any], password: str) -> bool:
    try:
        ok = bool(state["validate_admin_password"](password))
    except Exception:
        ok = False
    if ok:
        try:
            state["record_successful_password_ip"]()
        except Exception:
            pass
    return ok


def _validate_superadmin_password(state: Mapping[str, Any], password: str) -> bool:
    defaults, _web_conf_path, _app_dir = _load_env_defaults(state)
    expected_hash = str(defaults.get("MCWEB_SUPERADMIN_PASSWORD_HASH", "") or "").strip()
    if not expected_hash:
        expected_hash = str(defaults.get("MCWEB_ADMIN_PASSWORD_HASH", "") or "").strip()
    ok = False
    if expected_hash and password:
        try:
            ok = bool(check_password_hash(expected_hash, password))
        except Exception:
            ok = False
    if ok:
        try:
            state["record_successful_password_ip"]()
        except Exception:
            pass
    return ok


def _save_env_values(state: Mapping[str, Any], values: Mapping[str, object], web_conf_path: str | Path) -> tuple[bool, str]:
    try:
        setup_service_service.write_env_file(web_conf_path, values)
    except Exception as exc:
        try:
            state["log_mcweb_exception"]("panel_settings/save_env", exc)
        except Exception:
            pass
        return False, "Failed to write configuration."
    raw_values = _state_value(state, "WEB_CFG_VALUES")
    if isinstance(raw_values, dict):
        raw_values.clear()
        for key, value in values.items():
            raw_values[str(key)] = str(value).strip()
    return True, ""


def _parse_device_rows(rows: list[object]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "") or "").strip()
        ip = str(row.get("ip", "") or "").strip()
        if not name or not ip:
            continue
        mapping[ip] = name
    return mapping


def _load_device_fallmap(state: Mapping[str, Any]) -> dict[str, str]:
    db_path = _state_value(state, "APP_STATE_DB_PATH")
    if not db_path:
        return {}
    try:
        return state_store_service.load_fallmap(db_path)
    except Exception as exc:
        try:
            state["log_mcweb_exception"]("panel_settings/device_map_load", exc)
        except Exception:
            pass
    return {}


def _write_device_fallmap(state: Mapping[str, Any], mapping: dict[str, str]) -> bool:
    db_path = _state_value(state, "APP_STATE_DB_PATH")
    if not db_path:
        return False
    try:
        state_store_service.replace_fallmap(db_path, cast(Mapping[object, object], mapping))
        return True
    except Exception as exc:
        try:
            state["log_mcweb_exception"]("panel_settings/device_map_save", exc)
        except Exception:
            pass
    return False


def _parse_csv_upload(file_storage: Any) -> tuple[dict[str, str], list[str]]:
    if file_storage is None:
        return {}, ["CSV file is required."]
    try:
        text = file_storage.stream.read().decode("utf-8", errors="ignore")
    except Exception:
        return {}, ["Failed to read CSV file."]
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        return {}, ["CSV file has no headers."]
    mapping: dict[str, str] = {}
    errors: list[str] = []
    for row in reader:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Device name", "") or row.get("device_name", "") or row.get("name", "")).strip()
        ips_text = str(row.get("Tailscale IPs", "") or row.get("ips", "") or row.get("ip", "")).strip()
        if not name or not ips_text:
            continue
        for ip in [part.strip() for part in ips_text.split(",")]:
            if not ip:
                continue
            if ip in mapping and mapping[ip] != name:
                errors.append(f"Conflict in upload for {ip}: {mapping[ip]} vs {name}.")
            mapping[ip] = name
    return mapping, errors


def _merge_device_maps(
    existing: dict[str, str],
    incoming: dict[str, str],
    *,
    mode: str,
    resolution: str,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    base = {} if mode == "overwrite" else dict(existing)
    conflicts = []
    for ip, name in incoming.items():
        if ip in base and base[ip] != name:
            conflicts.append({"ip": ip, "existing": base[ip], "incoming": name})
            if resolution == "overwrite":
                base[ip] = name
            elif resolution in {"skip", "use_existing"}:
                continue
        else:
            base[ip] = name
    return base, conflicts


def register_panel_settings_routes(app: Any, state: Mapping[str, Any]) -> None:
    @app.route("/panel-settings")
    def panel_settings_page() -> Any:
        defaults, _web_conf_path, _app_dir = _load_env_defaults(state)
        panel_settings = {
            "display_tz": defaults.get("DISPLAY_TZ", ""),
            "minecraft_root_dir": defaults.get("MINECRAFT_ROOT_DIR", ""),
            "backup_dir": defaults.get("BACKUP_DIR", ""),
            "create_backup_dir": False,
            "require_password": _to_bool(defaults.get("MCWEB_REQUIRE_PASSWORD", "true")),
        }
        device_map = _load_device_fallmap(state)
        data_dir = _state_value(state, "DATA_DIR")
        if not data_dir:
            data_dir = _panel_app_dir(state) / "data"
        device_map_sample_path = str((Path(data_dir) / "list.csv").resolve())
        return render_shell_page_helper(
            app,
            state,
            render_template,
            "fragments/panel_settings_fragment.html",
            current_page="panel_settings",
            page_title="Panel Settings",
            csrf_token=state["_ensure_csrf_token"](),
            password_required=_to_bool(defaults.get("MCWEB_REQUIRE_PASSWORD", "true")),
            panel_settings=panel_settings,
            timezone_options=setup_queries_service.build_timezone_options(panel_settings["display_tz"]),
            device_map=device_map,
            device_map_sample_path=device_map_sample_path,
        )

    @app.route("/panel-settings/confirm-password", methods=["POST"])
    def panel_settings_confirm_password() -> Any:
        payload = request.get_json(silent=True) or {}
        password = str(payload.get("sudo_password", "") or "").strip()
        if not _validate_superadmin_password(state, password):
            return _json_fail("Password incorrect.", status=403, error="password_incorrect")
        return _json_ok()

    @app.route("/panel-settings/security", methods=["POST"])
    def panel_settings_security() -> Any:
        payload = request.get_json(silent=True) or {}
        password = str(payload.get("sudo_password", "") or "").strip()
        if not _validate_superadmin_password(state, password):
            return _json_fail("Password incorrect.", status=403, error="password_incorrect")
        require_password = _to_bool(payload.get("require_password"))
        new_password = str(payload.get("new_password", "") or "").strip()
        new_password_confirm = str(payload.get("new_password_confirm", "") or "").strip()
        new_superadmin_password = str(payload.get("new_superadmin_password", "") or "").strip()
        new_superadmin_password_confirm = str(payload.get("new_superadmin_password_confirm", "") or "").strip()

        defaults, web_conf_path, _app_dir = _load_env_defaults(state)
        had_superadmin_hash = bool(str(defaults.get("MCWEB_SUPERADMIN_PASSWORD_HASH", "") or "").strip())
        if new_password or new_password_confirm:
            if len(new_password) < 8:
                return _json_fail("Password must be at least 8 characters.", field_errors={"new_password": "Password must be at least 8 characters."})
            if new_password != new_password_confirm:
                return _json_fail("Passwords do not match.", field_errors={"new_password_confirm": "Passwords do not match."})
            defaults["MCWEB_ADMIN_PASSWORD_HASH"] = generate_password_hash(new_password)
        if new_superadmin_password or new_superadmin_password_confirm:
            if len(new_superadmin_password) < 8:
                return _json_fail("Superadmin password must be at least 8 characters.", field_errors={"new_superadmin_password": "Superadmin password must be at least 8 characters."})
            if new_superadmin_password != new_superadmin_password_confirm:
                return _json_fail("Superadmin passwords do not match.", field_errors={"new_superadmin_password_confirm": "Superadmin passwords do not match."})
            defaults["MCWEB_SUPERADMIN_PASSWORD_HASH"] = generate_password_hash(new_superadmin_password)
        elif not had_superadmin_hash:
            defaults["MCWEB_SUPERADMIN_PASSWORD_HASH"] = str(defaults.get("MCWEB_ADMIN_PASSWORD_HASH", "") or "").strip()
        defaults["MCWEB_REQUIRE_PASSWORD"] = "true" if require_password else "false"

        ok, message = _save_env_values(state, defaults, web_conf_path)
        if not ok:
            return _json_fail(message or "Failed to save security settings.")
        return _json_ok({"message": "Security settings saved."})

    @app.route("/panel-settings/paths", methods=["POST"])
    def panel_settings_paths() -> Any:
        payload = request.get_json(silent=True) or {}
        password = str(payload.get("sudo_password", "") or "").strip()
        if not _validate_superadmin_password(state, password):
            return _json_fail("Password incorrect.", status=403, error="password_incorrect")
        display_tz = str(payload.get("display_tz", "") or "").strip()
        minecraft_root_dir = str(payload.get("minecraft_root_dir", "") or "").strip()
        backup_dir = str(payload.get("backup_dir", "") or "").strip()
        create_backup_dir = _to_bool(payload.get("create_backup_dir"))

        field_errors: dict[str, str] = {}
        if not display_tz:
            field_errors["DISPLAY_TZ"] = "This field is required."
        if not minecraft_root_dir:
            field_errors["MINECRAFT_ROOT_DIR"] = "This field is required."
        if not backup_dir:
            field_errors["BACKUP_DIR"] = "This field is required."
        if field_errors:
            return _json_fail("Please fill in all required fields.", field_errors=field_errors)

        defaults, web_conf_path, _app_dir = _load_env_defaults(state)
        service_name = str(defaults.get("SERVICE", "minecraft") or "minecraft").strip()
        validation_errors: dict[str, str] = {}
        validation_message = ""
        validation_extra: dict[str, Any] = {}

        tz_result = setup_queries_service.validate_setup_request("timezone", {
            "DISPLAY_TZ": display_tz,
        })
        if not tz_result.get("ok"):
            validation_errors.update(tz_result.get("field_errors") or {})
            validation_message = tz_result.get("message", "") or validation_message

        root_result = setup_queries_service.validate_setup_request("root", {
            "SERVICE": service_name,
            "MINECRAFT_ROOT_DIR": minecraft_root_dir,
        })
        if not root_result.get("ok"):
            validation_errors.update(root_result.get("field_errors") or {})
            validation_message = root_result.get("message", "") or validation_message

        backup_result = setup_queries_service.validate_setup_request("backup", {
            "BACKUP_DIR": backup_dir,
            "CREATE_BACKUP_DIR": create_backup_dir,
        })
        if not backup_result.get("ok"):
            validation_errors.update(backup_result.get("field_errors") or {})
            validation_message = backup_result.get("message", "") or validation_message
            validation_extra = backup_result.get("extra") or validation_extra

        if validation_errors:
            return _json_fail(validation_message or "Validation failed.", field_errors=validation_errors, extra=validation_extra)

        defaults["DISPLAY_TZ"] = display_tz
        defaults["MINECRAFT_ROOT_DIR"] = minecraft_root_dir
        defaults["BACKUP_DIR"] = backup_dir
        defaults["CREATE_BACKUP_DIR"] = "true" if create_backup_dir else "false"

        ok, message = _save_env_values(state, defaults, web_conf_path)
        if not ok:
            return _json_fail(message or "Failed to save settings.")
        return _json_ok({"message": "Settings saved."})

    @app.route("/panel-settings/reboot", methods=["POST"])
    def panel_settings_reboot() -> Any:
        payload = request.get_json(silent=True) or {}
        password = str(payload.get("sudo_password", "") or "").strip()
        if not _validate_superadmin_password(state, password):
            return _json_fail("Password incorrect.", status=403, error="password_incorrect")
        _trigger_process_reload(state)
        return _json_ok({"message": "Rebooting app..."})

    @app.route("/panel-settings/device-map/save", methods=["POST"])
    def panel_settings_device_map_save() -> Any:
        payload = request.get_json(silent=True) or {}
        password = str(payload.get("sudo_password", "") or "").strip()
        if not _validate_superadmin_password(state, password):
            return _json_fail("Password incorrect.", status=403, error="password_incorrect")
        raw_rows = payload.get("rows")
        rows: list[object] = raw_rows if isinstance(raw_rows, list) else []
        mapping = _parse_device_rows(rows)
        if not _write_device_fallmap(state, mapping):
            return _json_fail("Failed to save device map.")
        return _json_ok({"device_map": mapping, "message": "Device map saved."})

    @app.route("/panel-settings/device-map/import", methods=["POST"])
    def panel_settings_device_map_import() -> Any:
        password = str(request.form.get("sudo_password", "") or "").strip()
        if not _validate_superadmin_password(state, password):
            return _json_fail("Password incorrect.", status=403, error="password_incorrect")
        mode = str(request.form.get("mode", "append") or "append").strip().lower()
        resolution = str(request.form.get("resolution", "") or "").strip().lower()
        if mode not in {"append", "overwrite"}:
            mode = "append"
        if resolution not in {"overwrite", "use_existing", "skip"}:
            resolution = ""
        incoming, parse_errors = _parse_csv_upload(request.files.get("file"))
        if parse_errors:
            return _json_fail("CSV parse error.", extra={"details": parse_errors})

        existing = _load_device_fallmap(state)
        merged, conflicts = _merge_device_maps(existing, incoming, mode=mode, resolution=resolution)
        if conflicts and not resolution:
            return _json_fail(
                "Conflicts detected in device map import.",
                status=409,
                error="conflict",
                extra={"conflicts": conflicts, "incoming": len(incoming), "existing": len(existing)},
            )
        if not _write_device_fallmap(state, merged):
            return _json_fail("Failed to import device map.")
        return _json_ok({
            "device_map": merged,
            "message": "Device map imported.",
            "conflicts": conflicts,
            "incoming": len(incoming),
            "existing": len(existing),
        })


__all__ = ["register_panel_settings_routes"]
