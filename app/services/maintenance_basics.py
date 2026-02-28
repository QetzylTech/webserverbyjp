"""Shared maintenance config, IO, and validation primitives."""

import copy
from datetime import datetime
import json
import shutil
from zoneinfo import ZoneInfo
from pathlib import Path

from flask import jsonify, request

_CLEANUP_SCHEMA_VERSION = 1
_CLEANUP_SCOPE_CHOICES = {"backups", "stale_worlds"}
_CLEANUP_EVENT_CHOICES = {"server_boot", "server_shutdown", "low_free_space"}
_CLEANUP_TIME_INTERVALS = {"daily", "weekly", "monthly", "every_n_days", "weekdays"}
_CLEANUP_ERROR_MESSAGES = {
    "validation_failure": "Validation failed. Please review the submitted values.",
    "ineligible_selection": "One or more selected files are no longer eligible for deletion.",
    "lock_held": "A cleanup run is already in progress. Try again shortly.",
    "guard_violation": "Cleanup guards blocked this operation to prevent destructive loss.",
    "schedule_conflict": "Schedule conflicts with an existing schedule at the same time.",
    "rules_disabled": "Rule-based cleanup is disabled.",
    "invalid_password": "Password incorrect.",
}


def _safe_int(value, default_value, minimum=0, maximum=10_000):
    """Handle safe int."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default_value
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _cleanup_data_dir(state):
    """Handle cleanup data dir."""
    return Path(state["session_state"].session_file).parent


def _cleanup_json_path(state):
    """Handle cleanup json path."""
    return _cleanup_data_dir(state) / "cleanup.json"


def _cleanup_non_normal_path(state):
    """Handle cleanup non normal path."""
    return _cleanup_data_dir(state) / "cleanup_non_normal.txt"


def _cleanup_history_path(state):
    """Handle cleanup history path."""
    return _cleanup_data_dir(state) / "cleanup history.json"


def _cleanup_log_path(state):
    """Handle cleanup log path."""
    return Path(state["MCWEB_LOG_FILE"]).parent / "cleanup.log"


def _cleanup_now_iso(state):
    """Handle cleanup now iso."""
    try:
        tz = state["DISPLAY_TZ"]
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).isoformat(timespec="seconds")


def _cleanup_atomic_write_json(path, payload):
    """Handle cleanup atomic write json."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _cleanup_atomic_write_text(path, text):
    """Handle cleanup atomic write text."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(str(text), encoding="utf-8")
    temp.replace(path)


def _cleanup_default_config():
    """Handle cleanup default config."""
    return {
        "schema_version": _CLEANUP_SCHEMA_VERSION,
        "rules": {
            "enabled": True,
            "categories": {
                "backup_zip": True,
                "stale_world_dir": True,
                "old_world_zip": True,
            },
            "age": {"enabled": True, "days": 7},
            "count": {
                "enabled": True,
                "max_per_category": 30,
                "session_backups_to_keep": 30,
                "manual_backups_to_keep": 30,
                "prerestore_backups_to_keep": 30,
            },
            "space": {
                "enabled": True,
                "used_trigger_percent": 80,
                "target_free_percent": 20,
                "hysteresis_percent": 5,
                "cooldown_seconds": 600,
                "free_space_below_gb": 0,
            },
            "time_based": {
                "time_of_backup": "03:00",
                "repeat_mode": "does_not_repeat",
                "weekly_day": "Sunday",
                "monthly_date": 1,
                "every_n_days": 1,
            },
            "guards": {
                "never_delete_newest_n_per_category": 1,
                "never_delete_last_backup_overall": True,
                "protect_active_world": True,
            },
            "caps": {
                "max_delete_files_absolute": 5,
                "max_delete_percent_eligible": 10,
                "max_delete_min_if_non_empty": 1,
            },
        },
        "schedules": [],
        "meta": {
            "rule_version": 1,
            "schedule_version": 1,
            "last_changed_by": "",
            "last_changed_at": "",
            "last_run_at": "",
            "last_run_trigger": "",
            "last_run_result": "",
            "last_run_deleted": 0,
            "last_run_errors": 0,
            "last_space_trigger_armed": True,
            "cooldown_until_unix": 0,
        },
        "scopes": {},
    }


def _cleanup_normalize_scope(raw_scope):
    """Normalize requested maintenance scope."""
    value = str(raw_scope or "").strip().lower()
    if value in _CLEANUP_SCOPE_CHOICES:
        return value
    return "backups"


def _cleanup_apply_scope_categories(rules, scope):
    """Apply hard category split by scope."""
    categories = rules.setdefault("categories", {})
    if scope == "stale_worlds":
        categories["backup_zip"] = False
        categories["stale_world_dir"] = True
        categories["old_world_zip"] = True
    else:
        categories["backup_zip"] = True
        categories["stale_world_dir"] = False
        categories["old_world_zip"] = False
    return rules


def _cleanup_get_scope_view(cfg, scope):
    """Get mutable per-scope view with rules/schedules/meta."""
    scope_key = _cleanup_normalize_scope(scope)
    scopes = cfg.setdefault("scopes", {})
    profile = scopes.get(scope_key)
    if not isinstance(profile, dict):
        profile = {
            "rules": copy.deepcopy(cfg.get("rules", _cleanup_default_config()["rules"])),
            "schedules": copy.deepcopy(cfg.get("schedules", [])),
            "meta": copy.deepcopy(cfg.get("meta", {})),
        }
        scopes[scope_key] = profile
    profile.setdefault("rules", copy.deepcopy(_cleanup_default_config()["rules"]))
    profile.setdefault("schedules", [])
    profile.setdefault("meta", {})
    return profile


def _cleanup_migrate_config_dict(state, loaded, default_cfg):
    """Migrate older cleanup.json shapes into the current schema."""
    cfg = default_cfg
    loaded_rules = loaded.get("rules") if isinstance(loaded, dict) else None
    if isinstance(loaded_rules, dict):
        for rule_key, rule_value in loaded_rules.items():
            if isinstance(cfg["rules"].get(rule_key), dict) and isinstance(rule_value, dict):
                cfg["rules"][rule_key].update(rule_value)
            else:
                cfg["rules"][rule_key] = rule_value
    if isinstance(loaded, dict) and isinstance(loaded.get("meta"), dict):
        cfg["meta"].update(loaded["meta"])
    if isinstance(loaded, dict) and isinstance(loaded.get("schedules"), list):
        cfg["schedules"] = loaded["schedules"]
    loaded_scopes = loaded.get("scopes") if isinstance(loaded, dict) else None
    if isinstance(loaded_scopes, dict):
        for scope_name in _CLEANUP_SCOPE_CHOICES:
            src = loaded_scopes.get(scope_name)
            if not isinstance(src, dict):
                continue
            dst = _cleanup_get_scope_view(cfg, scope_name)
            if isinstance(src.get("rules"), dict):
                dst["rules"] = copy.deepcopy(src["rules"])
            if isinstance(src.get("schedules"), list):
                dst["schedules"] = copy.deepcopy(src["schedules"])
            if isinstance(src.get("meta"), dict):
                dst["meta"] = copy.deepcopy(src["meta"])
    else:
        for scope_name in _CLEANUP_SCOPE_CHOICES:
            scoped = _cleanup_get_scope_view(cfg, scope_name)
            scoped["rules"] = copy.deepcopy(cfg.get("rules", default_cfg["rules"]))
            scoped["schedules"] = copy.deepcopy(cfg.get("schedules", []))
            scoped["meta"] = copy.deepcopy(cfg.get("meta", default_cfg["meta"]))

    rules = cfg.setdefault("rules", {})
    count = rules.setdefault("count", {})
    max_per = _safe_int(count.get("max_per_category", 30), 30, minimum=0, maximum=100000)
    count["session_backups_to_keep"] = _safe_int(count.get("session_backups_to_keep", max_per), max_per, minimum=0, maximum=100000)
    count["manual_backups_to_keep"] = _safe_int(count.get("manual_backups_to_keep", max_per), max_per, minimum=0, maximum=100000)
    count["prerestore_backups_to_keep"] = _safe_int(count.get("prerestore_backups_to_keep", max_per), max_per, minimum=0, maximum=100000)
    count["max_per_category"] = max(
        _safe_int(count.get("max_per_category", max_per), max_per, minimum=0, maximum=100000),
        count["session_backups_to_keep"],
        count["manual_backups_to_keep"],
        count["prerestore_backups_to_keep"],
    )

    space = rules.setdefault("space", {})
    space["free_space_below_gb"] = _safe_int(space.get("free_space_below_gb", 0), 0, minimum=0, maximum=1_000_000)

    time_based = rules.setdefault("time_based", {})
    time_based["time_of_backup"] = str(time_based.get("time_of_backup", "03:00"))
    time_based["repeat_mode"] = str(time_based.get("repeat_mode", "does_not_repeat")).strip().lower()
    if time_based["repeat_mode"] not in {"does_not_repeat", "daily", "weekly", "monthly", "weekdays", "every_n_days"}:
        time_based["repeat_mode"] = "does_not_repeat"
    time_based["weekly_day"] = str(time_based.get("weekly_day", "Sunday")).capitalize()
    if time_based["weekly_day"] not in {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"}:
        time_based["weekly_day"] = "Sunday"
    time_based["monthly_date"] = _safe_int(time_based.get("monthly_date", 1), 1, minimum=1, maximum=31)
    time_based["every_n_days"] = _safe_int(time_based.get("every_n_days", 1), 1, minimum=1, maximum=365)

    # Backfill time_based from first time schedule when legacy configs had schedules only.
    if time_based["repeat_mode"] == "does_not_repeat" and cfg.get("schedules"):
        first_time = None
        for item in cfg.get("schedules", []):
            if isinstance(item, dict) and str(item.get("mode", "")).strip().lower() == "time":
                first_time = item
                break
        if isinstance(first_time, dict):
            interval = str(first_time.get("interval", "daily")).strip().lower()
            interval_map = {
                "daily": "daily",
                "weekly": "weekly",
                "monthly": "monthly",
                "weekdays": "weekdays",
                "every_n_days": "every_n_days",
            }
            dow_map = {
                0: "Monday",
                1: "Tuesday",
                2: "Wednesday",
                3: "Thursday",
                4: "Friday",
                5: "Saturday",
                6: "Sunday",
            }
            time_based["time_of_backup"] = str(first_time.get("time", time_based["time_of_backup"]))
            time_based["repeat_mode"] = interval_map.get(interval, "daily")
            time_based["weekly_day"] = dow_map.get(_safe_int(first_time.get("day_of_week", 6), 6, minimum=0, maximum=6), "Sunday")
            time_based["monthly_date"] = _safe_int(first_time.get("day_of_month", 1), 1, minimum=1, maximum=31)
            time_based["every_n_days"] = _safe_int(first_time.get("every_n_days", 1), 1, minimum=1, maximum=365)

    cfg["schema_version"] = _CLEANUP_SCHEMA_VERSION
    for scope_name in _CLEANUP_SCOPE_CHOICES:
        scoped = _cleanup_get_scope_view(cfg, scope_name)
        scoped_rules = scoped.setdefault("rules", {})
        scoped_rules = _cleanup_apply_scope_from_state(state, scoped_rules, scope=scope_name)
        scoped["rules"] = _cleanup_apply_scope_categories(scoped_rules, scope_name)
        scoped.setdefault("schedules", [])
        scoped.setdefault("meta", {})
    return cfg


def _cleanup_default_non_normal():
    """Handle cleanup default non normal."""
    return {"missed_runs": [], "last_ack_at": "", "last_ack_by": ""}


def _cleanup_default_history():
    """Handle cleanup default history."""
    return {"runs": []}


def _cleanup_apply_scope_from_state(state, rules, scope=""):
    """Apply environment-defined safety/scope values onto rules."""
    categories = rules.setdefault("categories", {})
    guards = rules.setdefault("guards", {})
    if scope:
        _cleanup_apply_scope_categories(rules, _cleanup_normalize_scope(scope))
    else:
        categories["backup_zip"] = bool(state["MAINTENANCE_SCOPE_BACKUP_ZIP"])
        categories["stale_world_dir"] = bool(state["MAINTENANCE_SCOPE_STALE_WORLD_DIR"])
        categories["old_world_zip"] = bool(state["MAINTENANCE_SCOPE_OLD_WORLD_ZIP"])
    guards["never_delete_newest_n_per_category"] = _safe_int(state["MAINTENANCE_GUARD_NEVER_DELETE_NEWEST_N"], 1, minimum=0, maximum=1000)
    guards["never_delete_last_backup_overall"] = bool(state["MAINTENANCE_GUARD_NEVER_DELETE_LAST_BACKUP"])
    guards["protect_active_world"] = bool(state["MAINTENANCE_GUARD_PROTECT_ACTIVE_WORLD"])
    return rules


def _cleanup_load_config(state):
    """Handle cleanup load config."""
    path = _cleanup_json_path(state)
    default = _cleanup_default_config()
    if not path.exists():
        return default
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(loaded, dict):
        return default
    cfg = _cleanup_migrate_config_dict(state, loaded, default)
    try:
        # Persist migrated config once so future loads are clean and stable.
        if loaded != cfg:
            _cleanup_atomic_write_json(path, cfg)
    except Exception:
        pass
    cfg["rules"] = _cleanup_apply_scope_from_state(state, cfg.get("rules", {}))
    for scope_name in _CLEANUP_SCOPE_CHOICES:
        scoped = _cleanup_get_scope_view(cfg, scope_name)
        scoped_rules = _cleanup_apply_scope_from_state(state, scoped.get("rules", {}), scope=scope_name)
        scoped["rules"] = _cleanup_apply_scope_categories(scoped_rules, scope_name)
    return cfg


def _cleanup_load_non_normal(state):
    """Handle cleanup load non normal."""
    path = _cleanup_non_normal_path(state)
    default = _cleanup_default_non_normal()
    if not path.exists():
        return default
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(loaded, dict):
        return default
    data = default
    if isinstance(loaded.get("missed_runs"), list):
        data["missed_runs"] = loaded["missed_runs"]
    for key in ("last_ack_at", "last_ack_by"):
        if isinstance(loaded.get(key), str):
            data[key] = loaded[key]
    return data


def _cleanup_get_client_ip(state):
    """Handle cleanup get client ip."""
    client_ip = ""
    try:
        client_ip = (state["_get_client_ip"]() or "").strip()
    except Exception:
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            client_ip = xff.split(",")[0].strip()
        if not client_ip:
            client_ip = (request.headers.get("X-Real-IP") or "").strip()
        if not client_ip:
            client_ip = (request.remote_addr or "").strip()
    return client_ip


def _is_maintenance_allowed(state):
    """Return whether is maintenance allowed."""
    return True


def _cleanup_log(state, *, what, why, trigger, result, details=""):
    """Handle cleanup log."""
    stamp = _cleanup_now_iso(state)
    line = f"{stamp} | what={what} | why={why} | trigger={trigger} | result={result}"
    if details:
        line += f" | details={details}"
    line += "\n"
    try:
        path = _cleanup_log_path(state)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _cleanup_safe_used_percent(path):
    """Handle cleanup safe used percent."""
    try:
        usage = shutil.disk_usage(str(path))
    except OSError:
        return None, None, None
    total = int(usage.total)
    free = int(usage.free)
    used = total - free
    if total <= 0:
        return None, total, free
    return (100.0 * used / total), total, free


def _cleanup_mark_missed_run(state, reason, schedule_id="", scope=""):
    """Handle cleanup mark missed run."""
    data = _cleanup_load_non_normal(state)
    event = {
        "at": _cleanup_now_iso(state),
        "reason": str(reason),
        "schedule_id": str(schedule_id),
        "scope": _cleanup_normalize_scope(scope) if scope else "",
    }
    data["missed_runs"].append(event)
    data["missed_runs"] = data["missed_runs"][-100:]
    _cleanup_atomic_write_json(_cleanup_non_normal_path(state), data)


def _cleanup_load_history(state):
    """Handle cleanup load history."""
    path = _cleanup_history_path(state)
    default = _cleanup_default_history()
    if not path.exists():
        return default
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    if not isinstance(loaded, dict):
        return default
    runs = loaded.get("runs")
    if not isinstance(runs, list):
        return default
    normalized = []
    for item in runs:
        if isinstance(item, dict):
            normalized.append(item)
    return {"runs": normalized[-500:]}


def _cleanup_append_history(
    state,
    *,
    trigger,
    mode,
    dry_run,
    deleted_count,
    errors_count,
    requested_count=0,
    capped_count=0,
    result="ok",
    details="",
    scope="",
):
    """Append cleanup run history entry."""
    payload = _cleanup_load_history(state)
    runs = payload.setdefault("runs", [])
    runs.append(
        {
            "at": _cleanup_now_iso(state),
            "trigger": str(trigger),
            "mode": str(mode),
            "dry_run": bool(dry_run),
            "deleted_count": int(deleted_count or 0),
            "errors_count": int(errors_count or 0),
            "requested_count": int(requested_count or 0),
            "capped_count": int(capped_count or 0),
            "result": str(result),
            "details": str(details or ""),
            "scope": _cleanup_normalize_scope(scope) if scope else "",
        }
    )
    payload["runs"] = runs[-500:]
    _cleanup_atomic_write_json(_cleanup_history_path(state), payload)


def _cleanup_error(code, extra=None, status=400):
    """Handle cleanup error."""
    payload = {"ok": False, "error_code": code, "message": _CLEANUP_ERROR_MESSAGES.get(code, "Cleanup operation failed.")}
    if extra is not None:
        payload["details"] = extra
    return jsonify(payload), status


def is_maintenance_allowed(state):
    """Public helper used by route modules."""
    return _is_maintenance_allowed(state)
