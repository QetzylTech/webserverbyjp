"""Maintenance execution/runtime helpers."""

import threading
from typing import Any

from app.core import profiling
from app.services.maintenance_candidate_scan import CleanupCandidate, _cleanup_collect_candidates
from app.services.maintenance_context import as_ctx
from app.services.maintenance_rules import (
    _apply_blast_radius_cap,
    _apply_cleanup_targets,
    _apply_hard_guards,
    _build_cleanup_result,
    _build_protected_paths,
    _dedupe_oldest_first,
    _group_by_category,
    _select_cleanup_targets,
)

_cleanup_run_lock = threading.Lock()


def _cleanup_evaluate(
    ctx: Any,
    cfg: dict[str, Any],
    *,
    mode: str = "rule",
    selected_paths: list[str] | set[str] | None = None,
    apply_changes: bool = False,
    trigger: str = "manual_rule",
) -> dict[str, Any]:
    """Evaluate cleanup rules and return the deletion plan/result payload."""
    ctx = as_ctx(ctx)
    with profiling.timed("maintenance.evaluate.total"):
        selected_paths = {str(item) for item in (selected_paths or [])}
        rules = cfg.get("rules", {})
        if not isinstance(rules, dict):
            rules = {}
        with profiling.timed("maintenance.evaluate.candidate_discovery"):
            candidates = _cleanup_collect_candidates(ctx, cfg)
        if not isinstance(candidates, list):
            profiling.incr_error("maintenance.evaluate.candidate_discovery.invalid_result")
            candidates = []
        typed_candidates: list[CleanupCandidate] = candidates
        with profiling.timed("maintenance.evaluate.grouping"):
            by_category = _group_by_category(typed_candidates)
        with profiling.timed("maintenance.evaluate.hard_guards"):
            protected = _build_protected_paths(typed_candidates, by_category, rules)
            eligible = _apply_hard_guards(typed_candidates, protected)
        with profiling.timed("maintenance.evaluate.rule_selection"):
            to_delete, reasons_map = _select_cleanup_targets(
                ctx,
                cfg,
                mode=mode,
                candidates=typed_candidates,
                by_category=by_category,
                eligible=eligible,
                rules=rules,
                selected_paths=selected_paths,
            )
        with profiling.timed("maintenance.evaluate.dedup_and_blast_cap"):
            ordered = _dedupe_oldest_first(to_delete)
            eligible_count = len(eligible)
            capped_targets = _apply_blast_radius_cap(ordered, eligible_count, rules)
        with profiling.timed("maintenance.evaluate.apply_changes"):
            deleted, errors = _apply_cleanup_targets(capped_targets, apply_changes=apply_changes)
        with profiling.timed("maintenance.evaluate.output_build"):
            return _build_cleanup_result(
                candidates=typed_candidates,
                reasons_map=reasons_map,
                selected_paths=selected_paths,
                ordered=ordered,
                capped_targets=capped_targets,
                deleted=deleted,
                errors=errors,
                eligible_count=eligible_count,
                mode=mode,
                apply_changes=apply_changes,
            )


def _cleanup_run_with_lock(
    ctx: Any,
    cfg: dict[str, Any],
    *,
    mode: str,
    selected_paths: list[str] | set[str] | None = None,
    trigger: str = "manual_rule",
) -> dict[str, Any] | None:
    """Run cleanup once while holding the shared maintenance execution lock."""
    ctx = as_ctx(ctx)
    if not _cleanup_run_lock.acquire(blocking=False):
        return None
    try:
        return _cleanup_evaluate(
            ctx,
            cfg,
            mode=mode,
            selected_paths=selected_paths,
            apply_changes=True,
            trigger=trigger,
        )
    finally:
        _cleanup_run_lock.release()



def cleanup_lock_held() -> bool:
    """Return True when a cleanup run is currently executing."""
    try:
        return bool(_cleanup_run_lock.locked())
    except Exception:
        return False

