"""Maintenance selection and deletion helpers."""

import math
import time
from pathlib import Path
from typing import Any

from app.ports import ports
from app.services.maintenance_candidate_scan import CleanupCandidate, _backup_bucket
from app.services.maintenance_context import as_ctx
from app.services.maintenance_state_store import _cleanup_safe_used_percent, _safe_int


def _group_by_category(candidates: list[CleanupCandidate]) -> dict[str, list[CleanupCandidate]]:
    """Group candidates by category, newest first."""
    by_category: dict[str, list[CleanupCandidate]] = {}
    for row in candidates:
        by_category.setdefault(row["category"], []).append(row)
    for rows in by_category.values():
        rows.sort(key=lambda item: item["mtime"], reverse=True)
    return by_category


def _build_protected_paths(
    candidates: list[CleanupCandidate],
    by_category: dict[str, list[CleanupCandidate]],
    rules: dict[str, Any],
) -> set[str]:
    """Build protected paths from guard rules."""
    protected: set[str] = set()
    newest_n = _safe_int(rules.get("guards", {}).get("never_delete_newest_n_per_category", 1), 1, minimum=0, maximum=1000)
    if newest_n > 0:
        backup_rows = [row for row in by_category.get("backup_zip", [])]
        if backup_rows:
            by_bucket: dict[str, list[CleanupCandidate]] = {}
            for row in backup_rows:
                bucket = _backup_bucket(row.get("name", ""))
                by_bucket.setdefault(bucket, []).append(row)
            for rows in by_bucket.values():
                rows.sort(key=lambda item: item["mtime"], reverse=True)
                for row in rows[:newest_n]:
                    protected.add(row["path"])

        for category, rows in by_category.items():
            if category == "backup_zip":
                continue
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


def _apply_hard_guards(candidates: list[CleanupCandidate], protected: set[str]) -> list[CleanupCandidate]:
    """Apply hard guards and return eligible items."""
    eligible: list[CleanupCandidate] = []
    for row in candidates:
        if row["path"] in protected:
            row["eligible"] = False
            row["reasons"].append("hard_guard")
        if row["eligible"]:
            eligible.append(row)
    return eligible


def _mark(reasons_map: dict[str, set[str]], path: str, reason: str) -> None:
    reasons_map.setdefault(path, set()).add(reason)


def _add_age_targets(
    eligible: list[CleanupCandidate],
    rules: dict[str, Any],
    reasons_map: dict[str, set[str]],
    to_delete: list[CleanupCandidate],
) -> None:
    """Add age-based delete targets."""
    age_rule = rules.get("age", {})
    if not age_rule.get("enabled", True):
        return
    cutoff = time.time() - (_safe_int(age_rule.get("days", 3), 3, minimum=3, maximum=3650) * 86400)
    for row in eligible:
        if row["mtime"] <= cutoff:
            to_delete.append(row)
            _mark(reasons_map, row["path"], "age_rule")


def _add_count_targets(
    by_category: dict[str, list[CleanupCandidate]],
    rules: dict[str, Any],
    reasons_map: dict[str, set[str]],
    to_delete: list[CleanupCandidate],
) -> None:
    """Add count-based delete targets."""
    count_rule = rules.get("count", {})
    if not count_rule.get("enabled", True):
        return
    max_per_category = _safe_int(count_rule.get("max_per_category", 30), 30, minimum=0, maximum=100000)
    for rows in by_category.values():
        for idx, row in enumerate(rows):
            if idx >= max_per_category and row["eligible"]:
                to_delete.append(row)
                _mark(reasons_map, row["path"], "count_rule")


def _add_space_targets(
    ctx: Any,
    cfg: dict[str, Any],
    eligible: list[CleanupCandidate],
    rules: dict[str, Any],
    reasons_map: dict[str, set[str]],
    to_delete: list[CleanupCandidate],
) -> None:
    """Add space-based delete targets."""
    ctx = as_ctx(ctx)
    space_rule = rules.get("space", {})
    used_trigger = _safe_int(space_rule.get("used_trigger_percent", 80), 80, minimum=50, maximum=100)
    hysteresis = _safe_int(space_rule.get("hysteresis_percent", 5), 5, minimum=1, maximum=30)
    cooldown_seconds = _safe_int(space_rule.get("cooldown_seconds", 600), 600, minimum=0, maximum=86400)
    meta = cfg.setdefault("meta", {})

    used_percent, total_bytes, free_bytes = _cleanup_safe_used_percent(ctx.BACKUP_DIR)
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


def _space_rule_gate(ctx: Any, cfg: dict[str, Any], rules: dict[str, Any]) -> bool:
    """Return whether space-based cleanup gate is open."""
    ctx = as_ctx(ctx)
    space_rule = rules.get("space", {})
    if not space_rule.get("enabled", True):
        return True

    used_trigger = _safe_int(space_rule.get("used_trigger_percent", 80), 80, minimum=50, maximum=100)
    hysteresis = _safe_int(space_rule.get("hysteresis_percent", 5), 5, minimum=1, maximum=30)
    cooldown_seconds = _safe_int(space_rule.get("cooldown_seconds", 600), 600, minimum=0, maximum=86400)
    meta = cfg.setdefault("meta", {})

    used_percent, _, _ = _cleanup_safe_used_percent(ctx.BACKUP_DIR)
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


def _bucket_keep_limit(bucket: str, count_rule: dict[str, Any]) -> int:
    """Return keep limit for backup bucket."""
    default_limit = _safe_int(count_rule.get("max_per_category", 30), 30, minimum=3, maximum=100000)
    if bucket == "session":
        return _safe_int(count_rule.get("session_backups_to_keep", default_limit), default_limit, minimum=3, maximum=100000)
    if bucket == "manual":
        return _safe_int(count_rule.get("manual_backups_to_keep", default_limit), default_limit, minimum=3, maximum=100000)
    if bucket == "emergency":
        return _safe_int(count_rule.get("emergency_backups_to_keep", default_limit), default_limit, minimum=3, maximum=100000)
    if bucket == "pre_restore":
        return _safe_int(count_rule.get("prerestore_backups_to_keep", default_limit), default_limit, minimum=3, maximum=100000)
    return default_limit


def _add_backup_age_targets(
    backup_rows: list[CleanupCandidate],
    rules: dict[str, Any],
    reasons_map: dict[str, set[str]],
    to_delete: list[CleanupCandidate],
) -> None:
    """Add backup targets that satisfy the age rule."""
    age_rule = rules.get("age", {})
    if not age_rule.get("enabled", True):
        return
    cutoff = time.time() - (_safe_int(age_rule.get("days", 3), 3, minimum=3, maximum=3650) * 86400)
    for row in backup_rows:
        if row["mtime"] <= cutoff:
            to_delete.append(row)
            _mark(reasons_map, row["path"], "age_rule")


def _add_backup_count_targets(
    by_category: dict[str, list[CleanupCandidate]],
    rules: dict[str, Any],
    reasons_map: dict[str, set[str]],
    to_delete: list[CleanupCandidate],
) -> None:
    """Add backup targets that satisfy the count rule."""
    count_rule = rules.get("count", {})
    if not count_rule.get("enabled", True):
        return
    backup_by_bucket: dict[str, list[CleanupCandidate]] = {
        "session": [],
        "manual": [],
        "emergency": [],
        "pre_restore": [],
        "auto": [],
        "other": [],
    }
    for row in by_category.get("backup_zip", []):
        bucket = _backup_bucket(row["name"])
        backup_by_bucket.setdefault(bucket, []).append(row)
    for bucket, rows in backup_by_bucket.items():
        keep_limit = _bucket_keep_limit(bucket, count_rule)
        for idx, row in enumerate(rows):
            if idx >= keep_limit and row["eligible"]:
                to_delete.append(row)
                _mark(reasons_map, row["path"], "count_rule")


def _add_backup_space_targets(
    ctx: Any,
    cfg: dict[str, Any],
    backup_rows: list[CleanupCandidate],
    rules: dict[str, Any],
    reasons_map: dict[str, set[str]],
    to_delete: list[CleanupCandidate],
) -> None:
    """Add backup targets that satisfy the space rule."""
    if not rules.get("space", {}).get("enabled", True):
        return
    _add_space_targets(ctx, cfg, backup_rows, rules, reasons_map, to_delete)


def _dedupe_oldest_first(rows: list[CleanupCandidate]) -> list[CleanupCandidate]:
    """Dedupe oldest first."""
    unique: dict[str, CleanupCandidate] = {}
    for row in rows:
        unique[row["path"]] = row
    return sorted(unique.values(), key=lambda item: item["mtime"])


def _apply_blast_radius_cap(
    ordered: list[CleanupCandidate],
    eligible_count: int,
    rules: dict[str, Any],
) -> list[CleanupCandidate]:
    """Apply blast radius cap."""
    caps = rules.get("caps", {})
    pct = _safe_int(caps.get("max_delete_percent_eligible", 50), 50, minimum=1, maximum=100)
    min_non_empty = _safe_int(caps.get("max_delete_min_if_non_empty", 1), 1, minimum=1, maximum=20)
    pct_cap = math.floor((eligible_count * pct) / 100.0)
    if eligible_count > 0:
        pct_cap = max(min_non_empty, pct_cap)
    cap = pct_cap if eligible_count > 0 else 0
    return ordered[:cap] if cap >= 0 else []


def _cleanup_delete_target(path: str, is_dir: bool) -> None:
    """Delete one cleanup target, dispatching to file or directory removal."""
    target = Path(path)
    if is_dir:
        ports.filesystem.rmtree(target)
    else:
        target.unlink(missing_ok=True)


def _build_output_items(
    candidates: list[CleanupCandidate],
    reasons_map: dict[str, set[str]],
    deleted_paths: set[str],
) -> list[dict[str, Any]]:
    """Build output items."""
    output_items: list[dict[str, Any]] = []
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


def _build_deleted_output_items(rows: list[CleanupCandidate]) -> list[dict[str, Any]]:
    """Build compact deleted-item rows for completion summaries."""
    deleted_items: list[dict[str, Any]] = []
    for row in rows:
        deleted_items.append(
            {
                "name": row["name"],
                "path": row["path"],
                "category": row["category"],
                "size": row["size"],
                "mtime": row["mtime"],
            }
        )
    return deleted_items


def _select_manual_targets(
    candidates: list[CleanupCandidate],
    selected_paths: set[str],
    reasons_map: dict[str, set[str]],
) -> list[CleanupCandidate]:
    to_delete: list[CleanupCandidate] = []
    for row in candidates:
        if row["path"] not in selected_paths:
            continue
        if row["eligible"]:
            to_delete.append(row)
            _mark(reasons_map, row["path"], "manual_selection")
        else:
            _mark(reasons_map, row["path"], "ineligible_selection")
    return to_delete


def _select_rule_targets(
    ctx: Any,
    cfg: dict[str, Any],
    candidates: list[CleanupCandidate],
    by_category: dict[str, list[CleanupCandidate]],
    eligible: list[CleanupCandidate],
    rules: dict[str, Any],
    reasons_map: dict[str, set[str]],
) -> list[CleanupCandidate]:
    to_delete: list[CleanupCandidate] = []
    backup_eligible = [row for row in eligible if row.get("category") == "backup_zip"]
    if backup_eligible:
        _add_backup_age_targets(backup_eligible, rules, reasons_map, to_delete)
        _add_backup_count_targets(by_category, rules, reasons_map, to_delete)
        _add_backup_space_targets(ctx, cfg, backup_eligible, rules, reasons_map, to_delete)

    non_backup_eligible = [row for row in eligible if row.get("category") != "backup_zip"]
    if not non_backup_eligible:
        return to_delete

    non_backup_candidates = [row for row in candidates if row.get("category") != "backup_zip"]
    non_backup_by_category = _group_by_category(non_backup_candidates)
    _add_age_targets(non_backup_eligible, rules, reasons_map, to_delete)
    _add_count_targets(non_backup_by_category, rules, reasons_map, to_delete)
    _add_space_targets(ctx, cfg, non_backup_eligible, rules, reasons_map, to_delete)
    return to_delete


def _select_cleanup_targets(
    ctx: Any,
    cfg: dict[str, Any],
    *,
    mode: str,
    candidates: list[CleanupCandidate],
    by_category: dict[str, list[CleanupCandidate]],
    eligible: list[CleanupCandidate],
    rules: dict[str, Any],
    selected_paths: set[str],
) -> tuple[list[CleanupCandidate], dict[str, set[str]]]:
    reasons_map: dict[str, set[str]] = {}
    if mode == "manual":
        to_delete = _select_manual_targets(candidates, selected_paths, reasons_map)
    else:
        to_delete = _select_rule_targets(ctx, cfg, candidates, by_category, eligible, rules, reasons_map)
    return to_delete, reasons_map


def _apply_cleanup_targets(
    capped_targets: list[CleanupCandidate],
    *,
    apply_changes: bool,
) -> tuple[list[CleanupCandidate], list[str]]:
    deleted: list[CleanupCandidate] = []
    errors: list[str] = []
    if apply_changes:
        for row in capped_targets:
            try:
                _cleanup_delete_target(row["path"], row["is_dir"])
                deleted.append(row)
            except OSError as exc:
                errors.append(f"{row['name']}: {exc}")
        return deleted, errors
    return list(capped_targets), errors


def _build_cleanup_result(
    *,
    candidates: list[CleanupCandidate],
    reasons_map: dict[str, set[str]],
    selected_paths: set[str],
    ordered: list[CleanupCandidate],
    capped_targets: list[CleanupCandidate],
    deleted: list[CleanupCandidate],
    errors: list[str],
    eligible_count: int,
    mode: str,
    apply_changes: bool,
) -> dict[str, Any]:
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
        "deleted_items": _build_deleted_output_items(deleted),
        "errors": errors,
        "items": output_items,
        "selected_ineligible": selected_ineligible,
    }
