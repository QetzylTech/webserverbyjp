"""Maintenance state/config storage, IO, and validation primitives."""

import copy
from datetime import datetime
import json
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo
from app.core import state_store as state_store_service
from app.ports import ports
from app.services.maintenance_context import as_ctx

_CLEANUP_SCHEMA_VERSION = 1
_CLEANUP_SCOPE_CHOICES = {"backups", "stale_worlds"}
_CLEANUP_ERROR_MESSAGES = {
    "validation_failure": "Validation failed. Please review the submitted values.",
    "ineligible_selection": "One or more selected files are no longer eligible for deletion.",
    "lock_held": "A cleanup run is already in progress. Try again shortly.",
    "conflict": "Cleanup is blocked while backup or restore is running.",
    "guard_violation": "Cleanup guards blocked this operation to prevent destructive loss.",
    "schedule_conflict": "Schedule conflicts with an existing schedule at the same time.",
    "rules_disabled": "Rule-based cleanup is disabled.",
    "invalid_password": "Password incorrect.",
}
_CLEANUP_CONFIG_CACHE_TTL_SECONDS = 1.5
_CLEANUP_CONFIG_CACHE_LOCK = threading.Lock()
_CLEANUP_CONFIG_CACHE = {}


_SCHEDULER_STATE_LOCK = threading.Lock()
_SCHEDULER_STATE = {"backups": {"last_tick": 0}, "stale_worlds": {"last_tick": 0}}



def _safe_int(value, default_value, minimum=0, maximum=10_000):
    """Parse an integer and clamp it to the configured bounds."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default_value
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _cleanup_data_dir(ctx):
    """Return the app data directory used for maintenance state files."""
    ctx = as_ctx(ctx)
    return Path(ctx.session_state.session_file).parent


def _cleanup_db_path(ctx):
    """Return sqlite state-db path for structured maintenance records."""
    ctx = as_ctx(ctx)
    return Path(ctx.APP_STATE_DB_PATH)


def _cleanup_non_normal_path(ctx):
    """Return the path used for non-normal cleanup run state."""
    ctx = as_ctx(ctx)
    return _cleanup_data_dir(ctx) / "cleanup_non_normal.txt"


def _cleanup_log_path(ctx):
    """Return the maintenance log file path."""
    ctx = as_ctx(ctx)
    return Path(ctx.MCWEB_LOG_FILE).parent / "cleanup.log"


def _cleanup_now_iso(ctx):
    """Return the current display-tz timestamp in ISO format."""
    ctx = as_ctx(ctx)
    try:
        tz = ctx.DISPLAY_TZ
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).isoformat(timespec="seconds")


def _cleanup_atomic_write_json(path, payload):
    """Atomically write JSON maintenance state to disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _cleanup_load_json_file(path, default):
    """Load JSON from disk, returning default when missing or malformed."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return loaded if isinstance(loaded, dict) else default


def _cleanup_default_config():
    """Return the default maintenance configuration payload."""
    return {
        "schema_version": _CLEANUP_SCHEMA_VERSION,
        "rules": {
            "enabled": True,
            "categories": {
                "backup_zip": True,
                "stale_world_dir": True,
                "old_world_zip": True,
            },
            "age": {"enabled": True, "days": 3},
            "count": {
                "enabled": True,
                "max_per_category": 30,
                "session_backups_to_keep": 30,
                "manual_backups_to_keep": 30,
                "prerestore_backups_to_keep": 30,
                "emergency_backups_to_keep": 30,
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
                "enabled": True,
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
                "max_delete_files_absolute": 10,
                "max_delete_percent_eligible": 50,
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


def _cleanup_migrate_config_dict(ctx, loaded, default_cfg):
    """Normalize cleanup config into the current schema."""
    ctx = as_ctx(ctx)
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

    rules = cfg.setdefault("rules", {})
    count = rules.setdefault("count", {})
    max_per = _safe_int(count.get("max_per_category", 30), 30, minimum=3, maximum=100000)
    count["session_backups_to_keep"] = _safe_int(count.get("session_backups_to_keep", max_per), max_per, minimum=3, maximum=100000)
    count["manual_backups_to_keep"] = _safe_int(count.get("manual_backups_to_keep", max_per), max_per, minimum=3, maximum=100000)
    count["prerestore_backups_to_keep"] = _safe_int(count.get("prerestore_backups_to_keep", max_per), max_per, minimum=3, maximum=100000)
    count["emergency_backups_to_keep"] = _safe_int(count.get("emergency_backups_to_keep", max_per), max_per, minimum=3, maximum=100000)
    count["max_per_category"] = max(
        _safe_int(count.get("max_per_category", max_per), max_per, minimum=3, maximum=100000),
        count["session_backups_to_keep"],
        count["manual_backups_to_keep"],
        count["prerestore_backups_to_keep"],
        count["emergency_backups_to_keep"],
    )

    space = rules.setdefault("space", {})
    space["used_trigger_percent"] = _safe_int(space.get("used_trigger_percent", 80), 80, minimum=50, maximum=100)
    space["target_free_percent"] = max(0, min(50, 100 - space["used_trigger_percent"]))
    space["free_space_below_gb"] = _safe_int(space.get("free_space_below_gb", 0), 0, minimum=0, maximum=1_000_000)

    caps = rules.setdefault("caps", {})
    caps["max_delete_files_absolute"] = _safe_int(caps.get("max_delete_files_absolute", 10), 10, minimum=10, maximum=500)
    caps["max_delete_percent_eligible"] = _safe_int(caps.get("max_delete_percent_eligible", 50), 50, minimum=50, maximum=100)
    caps["max_delete_min_if_non_empty"] = _safe_int(caps.get("max_delete_min_if_non_empty", 1), 1, minimum=1, maximum=20)

    age = rules.setdefault("age", {})
    age["days"] = _safe_int(age.get("days", 3), 3, minimum=3, maximum=3650)

    time_based = rules.setdefault("time_based", {})
    time_based["enabled"] = bool(time_based.get("enabled", True))
    time_based["time_of_backup"] = str(time_based.get("time_of_backup", "03:00"))
    time_based["repeat_mode"] = str(time_based.get("repeat_mode", "does_not_repeat")).strip().lower()
    if time_based["repeat_mode"] not in {"does_not_repeat", "daily", "weekly", "monthly", "weekdays", "every_n_days"}:
        time_based["repeat_mode"] = "does_not_repeat"
    time_based["weekly_day"] = str(time_based.get("weekly_day", "Sunday")).capitalize()
    if time_based["weekly_day"] not in {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"}:
        time_based["weekly_day"] = "Sunday"
    time_based["monthly_date"] = _safe_int(time_based.get("monthly_date", 1), 1, minimum=1, maximum=31)
    time_based["every_n_days"] = _safe_int(time_based.get("every_n_days", 1), 1, minimum=1, maximum=365)

    cfg["schema_version"] = _CLEANUP_SCHEMA_VERSION
    for scope_name in _CLEANUP_SCOPE_CHOICES:
        scoped = _cleanup_get_scope_view(cfg, scope_name)
        scoped_rules = scoped.setdefault("rules", {})
        scoped_rules = _cleanup_apply_scope_from_state(ctx, scoped_rules, scope=scope_name)
        scoped["rules"] = _cleanup_apply_scope_categories(scoped_rules, scope_name)
        scoped.setdefault("schedules", [])
        scoped.setdefault("meta", {})
    return _cleanup_normalize_config_bounds(cfg)


def _cleanup_default_non_normal():
    """Return the default non-normal cleanup run payload."""
    return {"missed_runs": [], "last_ack_at": "", "last_ack_by": ""}


def _cleanup_default_history():
    """Return the default cleanup history payload."""
    return {"runs": []}


def _cleanup_apply_scope_from_state(ctx, rules, scope=""):
    """Apply environment-defined safety/scope values onto rules."""
    ctx = as_ctx(ctx)
    categories = rules.setdefault("categories", {})
    guards = rules.setdefault("guards", {})
    if scope:
        _cleanup_apply_scope_categories(rules, _cleanup_normalize_scope(scope))
    else:
        categories["backup_zip"] = bool(ctx.MAINTENANCE_SCOPE_BACKUP_ZIP)
        categories["stale_world_dir"] = bool(ctx.MAINTENANCE_SCOPE_STALE_WORLD_DIR)
        categories["old_world_zip"] = bool(ctx.MAINTENANCE_SCOPE_OLD_WORLD_ZIP)
    guards["never_delete_newest_n_per_category"] = _safe_int(ctx.MAINTENANCE_GUARD_NEVER_DELETE_NEWEST_N, 1, minimum=1, maximum=1000)
    guards["never_delete_last_backup_overall"] = bool(ctx.MAINTENANCE_GUARD_NEVER_DELETE_LAST_BACKUP)
    guards["protect_active_world"] = bool(ctx.MAINTENANCE_GUARD_PROTECT_ACTIVE_WORLD)
    return rules


def _cleanup_normalize_rule_bounds(rules):
    """Clamp rule values to their hard guard minimums."""
    age = rules.setdefault("age", {})
    age["days"] = _safe_int(age.get("days", 3), 3, minimum=3, maximum=3650)

    count = rules.setdefault("count", {})
    if "max_per_category" in count:
        count["max_per_category"] = _safe_int(count.get("max_per_category", 3), 3, minimum=3, maximum=100000)
    if "session_backups_to_keep" in count:
        count["session_backups_to_keep"] = _safe_int(count.get("session_backups_to_keep", 3), 3, minimum=3, maximum=100000)
    if "manual_backups_to_keep" in count:
        count["manual_backups_to_keep"] = _safe_int(count.get("manual_backups_to_keep", 3), 3, minimum=3, maximum=100000)
    if "prerestore_backups_to_keep" in count:
        count["prerestore_backups_to_keep"] = _safe_int(count.get("prerestore_backups_to_keep", 3), 3, minimum=3, maximum=100000)
    if "emergency_backups_to_keep" in count:
        count["emergency_backups_to_keep"] = _safe_int(count.get("emergency_backups_to_keep", 3), 3, minimum=3, maximum=100000)

    caps = rules.setdefault("caps", {})
    caps["max_delete_files_absolute"] = _safe_int(caps.get("max_delete_files_absolute", 10), 10, minimum=10, maximum=500)
    caps["max_delete_percent_eligible"] = _safe_int(caps.get("max_delete_percent_eligible", 50), 50, minimum=50, maximum=100)
    caps["max_delete_min_if_non_empty"] = _safe_int(caps.get("max_delete_min_if_non_empty", 1), 1, minimum=1, maximum=20)
    return rules


def _cleanup_normalize_config_bounds(cfg):
    cfg["rules"] = _cleanup_normalize_rule_bounds(cfg.get("rules", {}))
    for scope_name in _CLEANUP_SCOPE_CHOICES:
        scoped = _cleanup_get_scope_view(cfg, scope_name)
        scoped["rules"] = _cleanup_normalize_rule_bounds(scoped.get("rules", {}))
    return cfg


def _cleanup_cached_config(cache_key, now):
    with _CLEANUP_CONFIG_CACHE_LOCK:
        cached = _CLEANUP_CONFIG_CACHE.get(cache_key)
        if not isinstance(cached, dict):
            return None
        if float(cached.get("expires_at", 0.0) or 0.0) < now:
            return None
        payload = cached.get("config")
        if not isinstance(payload, dict):
            return None
        # Return a defensive copy to prevent callers from mutating the cache.
        return copy.deepcopy(payload)


def _cleanup_store_cached_config(cache_key, config, now):
    with _CLEANUP_CONFIG_CACHE_LOCK:
        _CLEANUP_CONFIG_CACHE[cache_key] = {
            "expires_at": now + _CLEANUP_CONFIG_CACHE_TTL_SECONDS,
            "config": copy.deepcopy(config),
        }


def _cleanup_apply_runtime_scope_overrides(ctx, cfg):
    cfg["rules"] = _cleanup_apply_scope_from_state(ctx, cfg.get("rules", {}))
    for scope_name in _CLEANUP_SCOPE_CHOICES:
        scoped = _cleanup_get_scope_view(cfg, scope_name)
        scoped_rules = _cleanup_apply_scope_from_state(ctx, scoped.get("rules", {}), scope=scope_name)
        scoped["rules"] = _cleanup_apply_scope_categories(scoped_rules, scope_name)
    return cfg


def _cleanup_load_config(ctx):
    """Load and cache the maintenance configuration from persistent storage."""
    ctx = as_ctx(ctx)
    db_path = _cleanup_db_path(ctx)
    cache_key = str(db_path)
    now = time.time()

    cached = _cleanup_cached_config(cache_key, now)
    if cached is not None:
        return cached

    default = _cleanup_default_config()
    try:
        loaded = state_store_service.load_cleanup_config(db_path)
    except Exception:
        loaded = None
    if not isinstance(loaded, dict):
        _cleanup_store_cached_config(cache_key, default, now)
        return copy.deepcopy(default)

    cfg = _cleanup_migrate_config_dict(ctx, loaded, default)
    try:
        # Persist migrated config once so future loads are clean and stable.
        if loaded != cfg:
            _cleanup_save_config(ctx, cfg)
    except Exception:
        pass
    cfg = _cleanup_apply_runtime_scope_overrides(ctx, cfg)
    cfg = _cleanup_normalize_config_bounds(cfg)
    _cleanup_store_cached_config(cache_key, cfg, now)
    return copy.deepcopy(cfg)


def _cleanup_save_config(ctx, payload):
    """Persist cleanup config to sqlite."""
    ctx = as_ctx(ctx)
    db_path = _cleanup_db_path(ctx)
    state_store_service.save_cleanup_config(db_path, payload)
    cache_key = str(db_path)
    with _CLEANUP_CONFIG_CACHE_LOCK:
        _CLEANUP_CONFIG_CACHE.pop(cache_key, None)


def _cleanup_load_non_normal(ctx):
    """Load the non-normal cleanup run payload from disk."""
    ctx = as_ctx(ctx)
    path = _cleanup_non_normal_path(ctx)
    default = _cleanup_default_non_normal()
    loaded = _cleanup_load_json_file(path, default)
    data = copy.deepcopy(default)
    if isinstance(loaded.get("missed_runs"), list):
        data["missed_runs"] = loaded["missed_runs"]
    for key in ("last_ack_at", "last_ack_by"):
        if isinstance(loaded.get(key), str):
            data[key] = loaded[key]
    return data


def get_cleanup_meta(ctx, scope="backups"):
    """Return cleanup meta fields for the requested scope."""
    cfg = _cleanup_load_config(ctx)
    scope_view = _cleanup_get_scope_view(cfg, scope)
    meta = scope_view.get("meta", {}) if isinstance(scope_view, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "last_run_at": str(meta.get("last_run_at", "") or ""),
        "rule_version": meta.get("rule_version"),
        "schedule_version": meta.get("schedule_version"),
        "last_changed_by": str(meta.get("last_changed_by", "") or ""),
    }


def get_cleanup_missed_run_count(ctx):
    """Return count of missed cleanup runs from non-normal tracking."""
    data = _cleanup_load_non_normal(ctx)
    missed = data.get("missed_runs") if isinstance(data, dict) else None
    if isinstance(missed, list):
        pending = 0
        for entry in missed:
            if not isinstance(entry, dict):
                pending += 1
                continue
            if entry.get("acknowledged") or entry.get("acknowledged_at") or entry.get("acknowledgedAt"):
                continue
            pending += 1
        return pending
    return 0


def _cleanup_get_client_ip(ctx):
    """Resolve the client IP for maintenance actions."""
    ctx = as_ctx(ctx)
    getter = getattr(ctx, "_get_client_ip", None)
    if callable(getter):
        try:
            return str(getter() or "").strip()
        except Exception:
            return ""
    return ""


def _cleanup_log(ctx, *, what, why, trigger, result, details=""):
    """Append one maintenance log record to disk."""
    ctx = as_ctx(ctx)
    stamp = _cleanup_now_iso(ctx)
    line = f"{stamp} | what={what} | why={why} | trigger={trigger} | result={result}"
    if details:
        line += f" | details={details}"
    line += "\n"
    try:
        path = _cleanup_log_path(ctx)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _cleanup_safe_used_percent(path):
    """Return used-percent and capacity numbers for the backup filesystem."""
    try:
        total, _used, free = ports.filesystem.disk_usage(path)
    except OSError:
        return None, None, None
    total = int(total)
    free = int(free)
    used = total - free
    if total <= 0:
        return None, total, free
    return (100.0 * used / total), total, free


def _cleanup_record_scheduler_tick(ctx, scope, now_ts, *, max_gap_seconds=75):
    """Update scheduler tick state and mark missed runs on gaps."""
    scope_key = _cleanup_normalize_scope(scope) if scope else "backups"
    with _SCHEDULER_STATE_LOCK:
        last_tick = int(_SCHEDULER_STATE.get(scope_key, {}).get("last_tick", 0) or 0)
        if scope_key not in _SCHEDULER_STATE:
            _SCHEDULER_STATE[scope_key] = {"last_tick": last_tick}
        _SCHEDULER_STATE[scope_key]["last_tick"] = int(now_ts or 0)
    if last_tick > 0 and int(now_ts or 0) - last_tick > int(max_gap_seconds or 0):
        _cleanup_mark_missed_run(ctx, "scheduler_gap", schedule_id=f"{scope_key}:scheduler", scope=scope_key)
    return last_tick


def _cleanup_mark_missed_run(ctx, reason, schedule_id="", scope=""):
    """Record a missed cleanup run for scheduler diagnostics."""
    ctx = as_ctx(ctx)
    data = _cleanup_load_non_normal(ctx)
    event = {
        "at": _cleanup_now_iso(ctx),
        "reason": str(reason),
        "schedule_id": str(schedule_id),
        "scope": _cleanup_normalize_scope(scope) if scope else "",
    }
    data["missed_runs"].append(event)
    data["missed_runs"] = data["missed_runs"][-100:]
    _cleanup_atomic_write_json(_cleanup_non_normal_path(ctx), data)


def _cleanup_load_history(ctx):
    """Load cleanup run history from persistent storage."""
    ctx = as_ctx(ctx)
    default = _cleanup_default_history()
    db_path = _cleanup_db_path(ctx)
    try:
        runs = state_store_service.load_cleanup_history_runs(db_path, limit=500)
        return {"runs": runs[-500:]}
    except Exception:
        return default


def _cleanup_save_history(ctx, payload):
    """Persist cleanup history document to sqlite."""
    ctx = as_ctx(ctx)
    db_path = _cleanup_db_path(ctx)
    runs = payload.get("runs") if isinstance(payload, dict) else []
    state_store_service.save_cleanup_history_runs(db_path, runs, max_rows=500)


def _cleanup_append_history(
    ctx,
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
    ctx = as_ctx(ctx)
    item = {
        "at": _cleanup_now_iso(ctx),
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
    db_path = _cleanup_db_path(ctx)
    state_store_service.append_cleanup_history_run(db_path, item, max_rows=500)


def _cleanup_error(code, extra=None, status=400):
    """Build a consistent error response payload for maintenance endpoints."""
    payload = {"ok": False, "error_code": code, "message": _CLEANUP_ERROR_MESSAGES.get(code, "Cleanup operation failed.")}
    if extra is not None:
        payload["details"] = extra
    return payload, status




