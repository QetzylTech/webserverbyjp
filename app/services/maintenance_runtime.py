"""Maintenance execution/runtime helpers."""

import math
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from app.services.maintenance_basics import (
    _cleanup_load_history,
    _cleanup_load_non_normal,
    _cleanup_safe_used_percent,
    _safe_int,
)
from app.services.maintenance_candidates import _backup_bucket, _cleanup_collect_candidates

_cleanup_run_lock = threading.Lock()


def _group_by_category(candidates):
    """Group by category."""
    by_category = {}
    for row in candidates:
        by_category.setdefault(row["category"], []).append(row)
    for rows in by_category.values():
        rows.sort(key=lambda item: item["mtime"], reverse=True)
    return by_category


def _build_protected_paths(candidates, by_category, rules):
    """Build protected paths."""
    protected = set()
    newest_n = _safe_int(rules.get("guards", {}).get("never_delete_newest_n_per_category", 1), 1, minimum=0, maximum=1000)
    if newest_n > 0:
        for rows in by_category.values():
            for row in rows[:newest_n]:
                protected.add(row["path"])

    if rules.get("guards", {}).get("never_delete_last_backup_overall", True):
        newest = None
        for row in candidates:
            if newest is None or row["mtime"] > newest["mtime"]:
                newest = row
        if newest is not None:
            protected.add(newest["path"])
    return protected


def _apply_hard_guards(candidates, protected):
    """Apply hard guards."""
    eligible = []
    for row in candidates:
        if row["path"] in protected:
            row["eligible"] = False
            row["reasons"].append("hard_guard")
        if row["eligible"]:
            eligible.append(row)
    return eligible


def _mark(reasons_map, path, reason):
    """Mark ."""
    reasons_map.setdefault(path, set()).add(reason)


def _add_age_targets(eligible, rules, reasons_map, to_delete):
    """Add age targets."""
    age_rule = rules.get("age", {})
    if not age_rule.get("enabled", True):
        return
    cutoff = time.time() - (_safe_int(age_rule.get("days", 7), 7, minimum=7, maximum=3650) * 86400)
    for row in eligible:
        if row["mtime"] <= cutoff:
            to_delete.append(row)
            _mark(reasons_map, row["path"], "age_rule")


def _add_count_targets(by_category, rules, reasons_map, to_delete):
    """Add count targets."""
    count_rule = rules.get("count", {})
    if not count_rule.get("enabled", True):
        return
    max_per_category = _safe_int(count_rule.get("max_per_category", 30), 30, minimum=0, maximum=100000)
    for rows in by_category.values():
        for idx, row in enumerate(rows):
            if idx >= max_per_category and row["eligible"]:
                to_delete.append(row)
                _mark(reasons_map, row["path"], "count_rule")


def _add_space_targets(state, cfg, eligible, rules, reasons_map, to_delete):
    """Add space targets."""
    space_rule = rules.get("space", {})
    used_trigger = _safe_int(space_rule.get("used_trigger_percent", 80), 80, minimum=50, maximum=100)
    hysteresis = _safe_int(space_rule.get("hysteresis_percent", 5), 5, minimum=1, maximum=30)
    cooldown_seconds = _safe_int(space_rule.get("cooldown_seconds", 600), 600, minimum=0, maximum=86400)
    meta = cfg.setdefault("meta", {})

    used_percent, total_bytes, free_bytes = _cleanup_safe_used_percent(state["BACKUP_DIR"])
    armed = bool(meta.get("last_space_trigger_armed", True))
    now_unix = int(time.time())
    cooldown_until = _safe_int(meta.get("cooldown_until_unix", 0), 0, minimum=0, maximum=2_147_483_647)

    if used_percent is not None:
        if used_percent <= max(0, used_trigger - hysteresis):
            armed = True
        meta["last_space_trigger_armed"] = armed

    should_run = (
        space_rule.get("enabled", True)
        and used_percent is not None
        and used_percent >= used_trigger
        and armed
        and now_unix >= cooldown_until
    )
    if not (should_run and total_bytes and free_bytes is not None):
        return

    free_space_below_gb = _safe_int(space_rule.get("free_space_below_gb", 0), 0, minimum=0, maximum=1_000_000)
    if free_space_below_gb > 0:
        target_free_bytes = int(free_space_below_gb * 1024 * 1024 * 1024)
        target_free_percent = max(0, min(50, int((target_free_bytes / max(1, total_bytes)) * 100)))
    else:
        target_free_percent = max(0, min(50, 100 - used_trigger))
        target_free_bytes = int(total_bytes * (target_free_percent / 100.0))
    already_selected = {row["path"] for row in to_delete}
    simulated_free = int(free_bytes) + sum(int(row["size"]) for row in to_delete)

    for row in sorted(eligible, key=lambda item: item["mtime"]):
        if simulated_free >= target_free_bytes:
            break
        if row["path"] in already_selected:
            continue
        to_delete.append(row)
        already_selected.add(row["path"])
        simulated_free += int(row["size"])
        _mark(reasons_map, row["path"], "space_reclaim")

    meta["last_space_trigger_armed"] = False
    meta["cooldown_until_unix"] = now_unix + cooldown_seconds


def _space_rule_gate(state, cfg, rules):
    """Return whether space-based cleanup gate is open."""
    space_rule = rules.get("space", {})
    if not space_rule.get("enabled", True):
        return True

    used_trigger = _safe_int(space_rule.get("used_trigger_percent", 80), 80, minimum=50, maximum=100)
    hysteresis = _safe_int(space_rule.get("hysteresis_percent", 5), 5, minimum=1, maximum=30)
    cooldown_seconds = _safe_int(space_rule.get("cooldown_seconds", 600), 600, minimum=0, maximum=86400)
    meta = cfg.setdefault("meta", {})

    used_percent, _, _ = _cleanup_safe_used_percent(state["BACKUP_DIR"])
    armed = bool(meta.get("last_space_trigger_armed", True))
    now_unix = int(time.time())
    cooldown_until = _safe_int(meta.get("cooldown_until_unix", 0), 0, minimum=0, maximum=2_147_483_647)

    if used_percent is not None and used_percent <= max(0, used_trigger - hysteresis):
        armed = True
    meta["last_space_trigger_armed"] = armed

    should_run = (
        used_percent is not None
        and used_percent >= used_trigger
        and armed
        and now_unix >= cooldown_until
    )
    if should_run:
        meta["last_space_trigger_armed"] = False
        meta["cooldown_until_unix"] = now_unix + cooldown_seconds
    return bool(should_run)


def _bucket_keep_limit(bucket, count_rule):
    """Return keep limit for backup bucket."""
    fallback = _safe_int(count_rule.get("max_per_category", 30), 30, minimum=3, maximum=100000)
    if bucket == "session":
        return _safe_int(count_rule.get("session_backups_to_keep", fallback), fallback, minimum=3, maximum=100000)
    if bucket == "manual":
        return _safe_int(count_rule.get("manual_backups_to_keep", fallback), fallback, minimum=3, maximum=100000)
    if bucket == "pre_restore":
        return _safe_int(count_rule.get("prerestore_backups_to_keep", fallback), fallback, minimum=3, maximum=100000)
    return fallback


def _add_backup_targets_all_rules(state, cfg, candidates, by_category, rules, reasons_map, to_delete):
    """Add backup targets that satisfy all enabled rules."""
    backup_rows = [row for row in candidates if row["eligible"] and row.get("category") == "backup_zip"]
    if not backup_rows:
        return

    age_rule = rules.get("age", {})
    count_rule = rules.get("count", {})
    age_enabled = bool(age_rule.get("enabled", True))
    count_enabled = bool(count_rule.get("enabled", True))
    space_enabled = bool(rules.get("space", {}).get("enabled", True))
    space_ok = _space_rule_gate(state, cfg, rules) if space_enabled else True

    cutoff = None
    if age_enabled:
        cutoff = time.time() - (_safe_int(age_rule.get("days", 7), 7, minimum=0, maximum=3650) * 86400)

    backup_by_bucket = {"session": [], "manual": [], "pre_restore": [], "auto": [], "other": []}
    for row in by_category.get("backup_zip", []):
        bucket = _backup_bucket(row["name"])
        backup_by_bucket.setdefault(bucket, []).append(row)

    count_allowed = {}
    for bucket, rows in backup_by_bucket.items():
        keep_limit = _bucket_keep_limit(bucket, count_rule)
        for idx, row in enumerate(rows):
            count_allowed[row["path"]] = idx >= keep_limit

    for row in backup_rows:
        age_ok = (not age_enabled) or (row["mtime"] <= cutoff)
        count_ok = (not count_enabled) or bool(count_allowed.get(row["path"], False))
        all_ok = age_ok and count_ok and space_ok
        if not all_ok:
            continue
        to_delete.append(row)
        if age_enabled:
            _mark(reasons_map, row["path"], "age_rule")
        if count_enabled:
            _mark(reasons_map, row["path"], "count_rule")
        if space_enabled:
            _mark(reasons_map, row["path"], "space_reclaim")


def _dedupe_oldest_first(rows):
    """Dedupe oldest first."""
    unique = {}
    for row in rows:
        unique[row["path"]] = row
    return sorted(unique.values(), key=lambda item: item["mtime"])


def _apply_blast_radius_cap(ordered, eligible_count, rules):
    """Apply blast radius cap."""
    caps = rules.get("caps", {})
    absolute_cap = _safe_int(caps.get("max_delete_files_absolute", 5), 5, minimum=1, maximum=500)
    pct = _safe_int(caps.get("max_delete_percent_eligible", 10), 10, minimum=1, maximum=100)
    min_non_empty = _safe_int(caps.get("max_delete_min_if_non_empty", 1), 1, minimum=1, maximum=20)
    pct_cap = math.floor((eligible_count * pct) / 100.0)
    if eligible_count > 0:
        pct_cap = max(min_non_empty, pct_cap)
    cap = min(absolute_cap, pct_cap if eligible_count > 0 else 0)
    return ordered[:cap] if cap >= 0 else []


def _cleanup_delete_target(path, is_dir):
    """Handle cleanup delete target."""
    target = Path(path)
    if is_dir:
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)


def _build_output_items(candidates, reasons_map, deleted_paths):
    """Build output items."""
    output_items = []
    for row in candidates:
        row_reasons = list(dict.fromkeys(row["reasons"] + sorted(reasons_map.get(row["path"], set()))))
        output_items.append(
            {
                "name": row["name"],
                "path": row["path"],
                "category": row["category"],
                "size": row["size"],
                "mtime": row["mtime"],
                "eligible": bool(row["eligible"]),
                "selected_for_delete": row["path"] in deleted_paths,
                "reasons": row_reasons,
            }
        )
    return output_items


def _cleanup_evaluate(state, cfg, *, mode="rule", selected_paths=None, apply_changes=False, trigger="manual_rule"):
    """Handle cleanup evaluate."""
    selected_paths = set(selected_paths or [])
    rules = cfg.get("rules", {})
    candidates = _cleanup_collect_candidates(state, cfg)
    by_category = _group_by_category(candidates)
    protected = _build_protected_paths(candidates, by_category, rules)
    eligible = _apply_hard_guards(candidates, protected)

    to_delete = []
    reasons_map = {}
    if mode == "manual":
        for row in candidates:
            if row["path"] in selected_paths and row["eligible"]:
                to_delete.append(row)
                _mark(reasons_map, row["path"], "manual_selection")
            elif row["path"] in selected_paths and not row["eligible"]:
                _mark(reasons_map, row["path"], "ineligible_selection")
    else:
        _add_backup_targets_all_rules(state, cfg, candidates, by_category, rules, reasons_map, to_delete)

        non_backup_eligible = [row for row in eligible if row.get("category") != "backup_zip"]
        if non_backup_eligible:
            non_backup_by_category = _group_by_category([row for row in candidates if row.get("category") != "backup_zip"])
            _add_age_targets(non_backup_eligible, rules, reasons_map, to_delete)
            _add_count_targets(non_backup_by_category, rules, reasons_map, to_delete)
            _add_space_targets(state, cfg, non_backup_eligible, rules, reasons_map, to_delete)

    ordered = _dedupe_oldest_first(to_delete)
    eligible_count = len(eligible)
    capped_targets = _apply_blast_radius_cap(ordered, eligible_count, rules)

    deleted = []
    errors = []
    if apply_changes:
        for row in capped_targets:
            try:
                _cleanup_delete_target(row["path"], row["is_dir"])
                deleted.append(row)
            except OSError as exc:
                errors.append(f"{row['name']}: {exc}")
    else:
        deleted = list(capped_targets)

    deleted_paths = {row["path"] for row in capped_targets}
    output_items = _build_output_items(candidates, reasons_map, deleted_paths)
    deleted_path_set = {row["path"] for row in deleted}
    selected_ineligible = sorted(
        path
        for path in selected_paths
        if path not in deleted_path_set and path in reasons_map and "ineligible_selection" in reasons_map[path]
    )

    return {
        "ok": True,
        "mode": mode,
        "apply_changes": bool(apply_changes),
        "eligible_count": eligible_count,
        "requested_delete_count": len(ordered),
        "capped_delete_count": len(capped_targets),
        "deleted_count": len(deleted),
        "deleted_bytes": sum(int(row["size"]) for row in deleted),
        "errors": errors,
        "items": output_items,
        "selected_ineligible": selected_ineligible,
    }


def _cleanup_state_snapshot(state, cfg):
    """Handle cleanup state snapshot."""
    def _next_time_schedule_run():
        tz = state.get("DISPLAY_TZ")
        now_local = datetime.now(tz) if tz else datetime.now()
        schedules = cfg.get("schedules", [])
        next_candidates = []
        has_event_schedule = False

        for schedule in schedules:
            if not isinstance(schedule, dict) or not schedule.get("enabled", True):
                continue
            mode = str(schedule.get("mode", "")).strip().lower()
            if mode == "event":
                has_event_schedule = True
                continue
            if mode != "time":
                continue

            raw_time = str(schedule.get("time", "03:00")).strip()
            try:
                hour, minute = [int(part) for part in raw_time.split(":", 1)]
            except Exception:
                continue
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                continue

            interval = str(schedule.get("interval", "daily")).strip().lower()
            weekly_day = _safe_int(schedule.get("day_of_week", 0), 0, minimum=0, maximum=6)
            monthly_day = _safe_int(schedule.get("day_of_month", 1), 1, minimum=1, maximum=31)
            every_n = _safe_int(schedule.get("every_n_days", 1), 1, minimum=1, maximum=365)
            anchor_raw = str(schedule.get("anchor_date", now_local.date().isoformat()))
            try:
                anchor_date = datetime.fromisoformat(anchor_raw).date()
            except Exception:
                anchor_date = now_local.date()

            for day_offset in range(0, 400):
                run_date = now_local.date() + timedelta(days=day_offset)
                due = False
                if interval == "daily":
                    due = True
                elif interval == "weekly":
                    due = run_date.weekday() == weekly_day
                elif interval == "monthly":
                    due = run_date.day == monthly_day
                elif interval == "weekdays":
                    due = run_date.weekday() in {0, 1, 2, 3, 4}
                elif interval == "every_n_days":
                    due = ((run_date - anchor_date).days % every_n) == 0
                if not due:
                    continue
                candidate = datetime(
                    run_date.year,
                    run_date.month,
                    run_date.day,
                    hour,
                    minute,
                    tzinfo=now_local.tzinfo,
                )
                if candidate >= now_local:
                    next_candidates.append(candidate)
                    break

        if next_candidates:
            return min(next_candidates).isoformat(timespec="seconds")
        if has_event_schedule:
            return "On event trigger"
        return "-"

    used_percent, total_bytes, free_bytes = _cleanup_safe_used_percent(state["BACKUP_DIR"])
    non_normal = _cleanup_load_non_normal(state)
    history = _cleanup_load_history(state)
    return {
        "config": cfg,
        "non_normal": non_normal,
        "history": history,
        "next_run_at": _next_time_schedule_run(),
        "storage": {
            "used_percent": used_percent,
            "total_bytes": total_bytes,
            "free_bytes": free_bytes,
        },
    }


def _cleanup_run_with_lock(state, cfg, *, mode, selected_paths=None, trigger="manual_rule"):
    """Handle cleanup run with lock."""
    if not _cleanup_run_lock.acquire(blocking=False):
        return None
    try:
        return _cleanup_evaluate(
            state,
            cfg,
            mode=mode,
            selected_paths=selected_paths,
            apply_changes=True,
            trigger=trigger,
        )
    finally:
        _cleanup_run_lock.release()

