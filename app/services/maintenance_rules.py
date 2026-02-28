"""Maintenance rules and schedule validation helpers."""

import re
from datetime import datetime

from app.services.maintenance_basics import (
    _CLEANUP_EVENT_CHOICES,
    _CLEANUP_TIME_INTERVALS,
    _cleanup_default_config,
    _safe_int,
)


def _cleanup_validate_rules(raw_rules):
    """Handle cleanup validate rules."""
    if not isinstance(raw_rules, dict):
        return False, "Rules payload must be an object."
    rules = _cleanup_default_config()["rules"]
    for top_key in ("categories", "age", "count", "space", "guards", "caps", "time_based"):
        if isinstance(raw_rules.get(top_key), dict):
            rules[top_key].update(raw_rules[top_key])
    if "enabled" in raw_rules:
        rules["enabled"] = bool(raw_rules["enabled"])

    try:
        used_trigger = int(rules["space"].get("used_trigger_percent", 80))
    except Exception:
        return False, "Space trigger must be an integer."
    free_space_below_gb = _safe_int(
        rules["space"].get("free_space_below_gb", 0),
        0,
        minimum=0,
        maximum=1_000_000,
    )
    if free_space_below_gb > 0:
        # Keep this within evaluator range by deriving a trigger later from storage totals.
        used_trigger = max(50, min(100, used_trigger))
    if used_trigger < 50 or used_trigger > 100:
        return False, "Space trigger must be between 50 and 100 percent used."
    rules["space"]["used_trigger_percent"] = used_trigger
    rules["space"]["free_space_below_gb"] = free_space_below_gb

    try:
        hysteresis = int(rules["space"].get("hysteresis_percent", 5))
    except Exception:
        return False, "Hysteresis must be an integer."
    if hysteresis < 1 or hysteresis > 30:
        return False, "Hysteresis must be between 1 and 30."
    rules["space"]["hysteresis_percent"] = hysteresis
    rules["space"]["target_free_percent"] = max(0, min(50, 100 - used_trigger))
    rules["space"]["cooldown_seconds"] = _safe_int(rules["space"].get("cooldown_seconds", 600), 600, minimum=0, maximum=86400)

    rules["age"]["days"] = _safe_int(rules["age"].get("days", 7), 7, minimum=0, maximum=3650)
    rules["count"]["session_backups_to_keep"] = _safe_int(
        rules["count"].get("session_backups_to_keep", rules["count"].get("max_per_category", 30)),
        30,
        minimum=0,
        maximum=100000,
    )
    rules["count"]["manual_backups_to_keep"] = _safe_int(
        rules["count"].get("manual_backups_to_keep", rules["count"].get("max_per_category", 30)),
        30,
        minimum=0,
        maximum=100000,
    )
    rules["count"]["prerestore_backups_to_keep"] = _safe_int(
        rules["count"].get("prerestore_backups_to_keep", rules["count"].get("max_per_category", 30)),
        30,
        minimum=0,
        maximum=100000,
    )
    rules["count"]["max_per_category"] = max(
        rules["count"]["session_backups_to_keep"],
        rules["count"]["manual_backups_to_keep"],
        rules["count"]["prerestore_backups_to_keep"],
        _safe_int(rules["count"].get("max_per_category", 30), 30, minimum=0, maximum=100000),
    )

    time_based = rules.setdefault("time_based", {})
    time_based["time_of_backup"] = str(time_based.get("time_of_backup", "03:00")).strip()
    if not re.match(r"^\d{2}:\d{2}$", time_based["time_of_backup"]):
        return False, "Time of backup must be HH:MM."
    hour = int(time_based["time_of_backup"][:2])
    minute = int(time_based["time_of_backup"][3:])
    if hour > 23 or minute > 59:
        return False, "Time of backup is out of range."
    repeat_mode = str(time_based.get("repeat_mode", "does_not_repeat")).strip().lower()
    valid_repeat = {"does_not_repeat", "daily", "weekly", "monthly", "weekdays", "every_n_days"}
    if repeat_mode not in valid_repeat:
        return False, "Repeat mode is invalid."
    time_based["repeat_mode"] = repeat_mode
    weekly_day = str(time_based.get("weekly_day", "Sunday")).strip().capitalize()
    if weekly_day not in {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"}:
        weekly_day = "Sunday"
    time_based["weekly_day"] = weekly_day
    time_based["monthly_date"] = _safe_int(time_based.get("monthly_date", 1), 1, minimum=1, maximum=31)
    time_based["every_n_days"] = _safe_int(time_based.get("every_n_days", 1), 1, minimum=1, maximum=365)
    rules["guards"]["never_delete_newest_n_per_category"] = _safe_int(
        rules["guards"].get("never_delete_newest_n_per_category", 1),
        1,
        minimum=0,
        maximum=1000,
    )
    rules["caps"]["max_delete_files_absolute"] = _safe_int(rules["caps"].get("max_delete_files_absolute", 5), 5, minimum=1, maximum=500)
    rules["caps"]["max_delete_percent_eligible"] = _safe_int(rules["caps"].get("max_delete_percent_eligible", 10), 10, minimum=1, maximum=100)
    rules["caps"]["max_delete_min_if_non_empty"] = _safe_int(rules["caps"].get("max_delete_min_if_non_empty", 1), 1, minimum=1, maximum=20)
    return True, rules


def _cleanup_validate_schedules(raw_schedules):
    """Handle cleanup validate schedules."""
    if raw_schedules is None:
        return True, []
    if not isinstance(raw_schedules, list):
        return False, "Schedules payload must be a list."
    schedules = []
    seen_time = set()
    for idx, entry in enumerate(raw_schedules):
        if not isinstance(entry, dict):
            return False, f"Schedule #{idx + 1} must be an object."
        mode = str(entry.get("mode", "time")).strip().lower()
        if mode not in {"time", "event"}:
            return False, f"Schedule #{idx + 1} mode is invalid."
        enabled = bool(entry.get("enabled", True))
        item = {"id": str(entry.get("id", f"sched-{idx + 1}")), "mode": mode, "enabled": enabled}
        if mode == "time":
            interval = str(entry.get("interval", "daily")).strip().lower()
            if interval not in (_CLEANUP_TIME_INTERVALS | {"weekdays"}):
                return False, f"Schedule #{idx + 1} interval is invalid."
            at_time = str(entry.get("time", "03:00")).strip()
            if not re.match(r"^\d{2}:\d{2}$", at_time):
                return False, f"Schedule #{idx + 1} time must be HH:MM."
            hour = int(at_time[:2])
            minute = int(at_time[3:])
            if hour > 23 or minute > 59:
                return False, f"Schedule #{idx + 1} time is out of range."
            if at_time in seen_time:
                return False, "At least two schedules conflict at the same effective time."
            seen_time.add(at_time)
            item.update(
                {
                    "interval": interval,
                    "time": at_time,
                    "day_of_week": _safe_int(entry.get("day_of_week", 0), 0, minimum=0, maximum=6),
                    "day_of_month": _safe_int(entry.get("day_of_month", 1), 1, minimum=1, maximum=31),
                    "every_n_days": _safe_int(entry.get("every_n_days", 1), 1, minimum=1, maximum=365),
                    "anchor_date": str(entry.get("anchor_date", datetime.utcnow().date().isoformat())),
                }
            )
        else:
            event = str(entry.get("event", "server_boot")).strip().lower()
            if event not in _CLEANUP_EVENT_CHOICES:
                return False, f"Schedule #{idx + 1} event is invalid."
            item["event"] = event
        schedules.append(item)
    return True, schedules


def _cleanup_schedule_due_now(schedule, now_local):
    """Handle cleanup schedule due now."""
    if not schedule.get("enabled", True):
        return False
    if schedule.get("mode") != "time":
        return False
    interval = schedule.get("interval", "daily")
    try:
        hour, minute = [int(part) for part in str(schedule.get("time", "03:00")).split(":", 1)]
    except Exception:
        return False
    if now_local.hour != hour or now_local.minute != minute:
        return False
    if interval == "daily":
        return True
    if interval == "weekly":
        return now_local.weekday() == int(schedule.get("day_of_week", 0))
    if interval == "monthly":
        return now_local.day == int(schedule.get("day_of_month", 1))
    if interval == "weekdays":
        return now_local.weekday() in {0, 1, 2, 3, 4}
    if interval == "every_n_days":
        every_n = max(1, int(schedule.get("every_n_days", 1)))
        anchor_raw = str(schedule.get("anchor_date", now_local.date().isoformat()))
        try:
            anchor = datetime.fromisoformat(anchor_raw).date()
        except Exception:
            anchor = now_local.date()
        return ((now_local.date() - anchor).days % every_n) == 0
    return False

