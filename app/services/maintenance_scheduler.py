"""Maintenance cleanup scheduler and event triggers."""

import threading
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.worker_scheduler import WorkerSpec, start_worker
from app.services.maintenance_context import as_ctx
from app.services.maintenance_conflicts import priority_conflict
from app.services.maintenance_engine import _cleanup_run_with_lock
from app.services.maintenance_policy import _cleanup_schedule_due_now
from app.services.maintenance_state_store import (
    _cleanup_append_history,
    _cleanup_atomic_write_json,
    _cleanup_get_scope_view,
    _cleanup_load_config,
    _cleanup_load_history,
    _cleanup_load_non_normal,
    _cleanup_log,
    _cleanup_mark_missed_run,
    _cleanup_non_normal_path,
    _cleanup_now_iso,
    _cleanup_save_config,
    _cleanup_save_history,
    _cleanup_safe_used_percent,
    _cleanup_record_scheduler_tick,
    _safe_int,
)

_cleanup_scheduler_start_lock = threading.Lock()
_cleanup_scheduler_started = False



def _save_run_result(
    ctx: Any,
    full_cfg: dict[str, Any],
    cfg: dict[str, Any],
    *,
    scope: str,
    trigger: str,
    result: dict[str, Any],
    why: str,
    what: str,
) -> None:
    """Persist cleanup run metadata, history, and log output for one execution."""
    meta = cfg.setdefault("meta", {})
    run_result = "ok" if not result["errors"] else "partial"
    meta["last_run_at"] = _cleanup_now_iso(ctx)
    meta["last_run_trigger"] = trigger
    meta["last_run_result"] = run_result
    meta["last_run_deleted"] = result["deleted_count"]
    meta["last_run_errors"] = len(result["errors"])
    _cleanup_append_history(
        ctx,
        trigger=trigger,
        mode="rule",
        dry_run=False,
        deleted_count=result["deleted_count"],
        errors_count=len(result["errors"]),
        requested_count=result.get("requested_delete_count", 0),
        capped_count=result.get("capped_delete_count", result["deleted_count"]),
        result=run_result,
        scope=scope,
    )
    _cleanup_log(
        ctx,
        what=what,
        why=why,
        trigger=trigger.split(":", 1)[1] if ":" in trigger else trigger,
        result=run_result,
        details=f"deleted={result['deleted_count']};errors={len(result['errors'])}",
    )
    _cleanup_save_config(ctx, full_cfg)


def _run_cleanup_trigger(
    ctx: Any,
    full_cfg: dict[str, Any],
    cfg: dict[str, Any],
    *,
    scope: str,
    trigger: str,
    schedule_id: str,
    why: str,
    what: str,
    extra_meta: dict[str, Any] | None = None,
) -> bool:
    """Run one cleanup trigger and record the outcome when work actually executes."""
    conflict_reason = priority_conflict(ctx)
    if conflict_reason:
        _cleanup_mark_missed_run(ctx, "priority_conflict", schedule_id=schedule_id, scope=scope)
        return False
    result = _cleanup_run_with_lock(ctx, cfg, mode="rule", trigger=trigger)
    if result is None:
        _cleanup_mark_missed_run(ctx, "lock_held", schedule_id=schedule_id, scope=scope)
        return False
    if extra_meta:
        cfg.setdefault("meta", {}).update(extra_meta)
    _save_run_result(ctx, full_cfg, cfg, scope=scope, trigger=trigger, result=result, why=why, what=what)
    return True


def _low_free_space_due(ctx: Any, cfg: dict[str, Any], schedule: dict[str, Any]) -> bool:
    """Return whether the low-free-space event threshold is currently met."""
    used_percent, _, _ = _cleanup_safe_used_percent(ctx.BACKUP_DIR)
    threshold = _safe_int(
        schedule.get("used_trigger_percent", cfg.get("rules", {}).get("space", {}).get("used_trigger_percent", 80)),
        80,
        minimum=50,
        maximum=100,
    )
    return used_percent is not None and used_percent >= threshold


def _cleanup_scheduler_loop(ctx: Any) -> None:
    """Poll configured schedules and run eligible cleanup jobs."""
    ctx = as_ctx(ctx)
    boot_event_done: set[str] = set()
    while True:
        try:
            full_cfg = _cleanup_load_config(ctx)
            tz = getattr(ctx, "DISPLAY_TZ", None) or ZoneInfo("UTC")
            now_local = datetime.now(tz)
            now_ts = int(time.time())

            for scope in ("backups", "stale_worlds"):
                cfg = _cleanup_get_scope_view(full_cfg, scope)
                schedules = cfg.get("schedules", [])
                meta = cfg.setdefault("meta", {})
                last_tick = _cleanup_record_scheduler_tick(ctx, scope, now_ts, max_gap_seconds=75)
                meta["last_scheduler_tick"] = now_ts

                for schedule in schedules:
                    if not schedule.get("enabled", True):
                        continue

                    mode = schedule.get("mode")
                    if mode == "event":
                        event_name = str(schedule.get("event", "")).strip().lower()
                        if event_name == "server_boot" and scope not in boot_event_done:
                            _run_cleanup_trigger(
                                ctx,
                                full_cfg,
                                cfg,
                                scope=scope,
                                trigger=f"scheduled:{scope}:server_boot",
                                schedule_id=f"{scope}:{schedule.get('id', '')}",
                                why="event",
                                what="scheduled_run",
                            )
                            boot_event_done.add(scope)
                        elif event_name == "low_free_space" and _low_free_space_due(ctx, cfg, schedule):
                            _run_cleanup_trigger(
                                ctx,
                                full_cfg,
                                cfg,
                                scope=scope,
                                trigger=f"scheduled:{scope}:low_free_space",
                                schedule_id=f"{scope}:{schedule.get('id', '')}",
                                why="event",
                                what="scheduled_run",
                            )
                        continue

                    if mode != "time" or not _cleanup_schedule_due_now(schedule, now_local):
                        continue

                    schedule_id = str(schedule.get("id", "")).strip()
                    key = f"last_schedule_run_{schedule_id}"
                    last_at = _safe_int(meta.get(key, 0), 0, minimum=0, maximum=2_147_483_647)
                    if now_ts - last_at < 50:
                        continue
                    _run_cleanup_trigger(
                        ctx,
                        full_cfg,
                        cfg,
                        scope=scope,
                        trigger=f"scheduled:{scope}:{schedule_id}",
                        schedule_id=f"{scope}:{schedule_id}",
                        why="time",
                        what="scheduled_run",
                        extra_meta={key: now_ts},
                    )
        except Exception:
            _cleanup_mark_missed_run(ctx, "scheduler_exception")
        time.sleep(30)


def _cleanup_start_scheduler_once(ctx: Any) -> None:
    """Start the cleanup scheduler once for the current process."""
    ctx = as_ctx(ctx)
    global _cleanup_scheduler_started
    with _cleanup_scheduler_start_lock:
        if _cleanup_scheduler_started:
            return
        cfg = _cleanup_load_config(ctx)
        try:
            _cleanup_save_config(ctx, cfg)
            _cleanup_atomic_write_json(_cleanup_non_normal_path(ctx), _cleanup_load_non_normal(ctx))
            _cleanup_save_history(ctx, _cleanup_load_history(ctx))
        except Exception as exc:
            try:
                logger = getattr(ctx, "log_mcweb_exception", None)
                if logger:
                    logger("cleanup_scheduler_bootstrap", exc)
            except Exception:
                pass
        start_worker(
            ctx,
            WorkerSpec(
                name="cleanup-scheduler",
                target=_cleanup_scheduler_loop,
                args=(ctx,),
                interval_source=30.0,
                stop_signal_name="cleanup_scheduler_stop_event",
                health_marker="cleanup_scheduler",
            ),
        )
        _cleanup_scheduler_started = True


def _cleanup_run_event_if_enabled(ctx: Any, event_name: object) -> None:
    """Run event-triggered cleanup for scopes that enable the given event."""
    ctx = as_ctx(ctx)
    full_cfg = _cleanup_load_config(ctx)
    normalized_event = str(event_name or "").strip().lower()
    for scope in ("backups", "stale_worlds"):
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        schedules = cfg.get("schedules", [])
        matched = [
            item
            for item in schedules
            if item.get("mode") == "event"
            and item.get("enabled", True)
            and str(item.get("event", "")).strip().lower() == normalized_event
        ]
        if not matched:
            continue
        _run_cleanup_trigger(
            ctx,
            full_cfg,
            cfg,
            scope=scope,
            trigger=f"event:{scope}:{normalized_event}",
            schedule_id=f"{scope}:{normalized_event}",
            why="event_trigger",
            what="event_run",
        )


start_cleanup_scheduler_once = _cleanup_start_scheduler_once
run_cleanup_event_if_enabled = _cleanup_run_event_if_enabled


def _schedule_next_time(now_local: datetime, schedule: dict[str, Any]) -> datetime | None:
    """Return next datetime for a time-based schedule, or None."""
    if not schedule.get("enabled", True):
        return None
    if schedule.get("mode") != "time":
        return None
    try:
        hour, minute = [int(part) for part in str(schedule.get("time", "03:00")).split(":", 1)]
    except Exception:
        return None
    base_today = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    interval = str(schedule.get("interval", "daily")).strip().lower()
    if interval == "daily":
        return base_today if base_today > now_local else (base_today + timedelta(days=1))
    if interval == "weekly":
        target = _safe_int(schedule.get("day_of_week", 0), 0, minimum=0, maximum=6) % 7
        delta = (target - now_local.weekday()) % 7
        candidate = base_today + timedelta(days=delta)
        if candidate <= now_local:
            candidate += timedelta(days=7)
        return candidate
    if interval == "monthly":
        day = _safe_int(schedule.get("day_of_month", 1), 1, minimum=1, maximum=31)
        if day < 1:
            day = 1
        try:
            candidate = base_today.replace(day=day)
        except ValueError:
            candidate = base_today.replace(day=1) + timedelta(days=31)
            candidate = candidate.replace(day=1) - timedelta(days=1)
            candidate = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_local:
            next_month = (now_local.replace(day=1) + timedelta(days=32)).replace(day=1)
            try:
                candidate = next_month.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
            except ValueError:
                candidate = (next_month + timedelta(days=31)).replace(day=1) - timedelta(days=1)
                candidate = candidate.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate
    if interval == "every_n_days":
        every_n = _safe_int(schedule.get("every_n_days", 1), 1, minimum=1, maximum=365)
        anchor_raw = str(schedule.get("anchor_date", now_local.date().isoformat()))
        try:
            anchor = datetime.fromisoformat(anchor_raw).date()
        except Exception:
            anchor = now_local.date()
        days_since = (now_local.date() - anchor).days
        if days_since < 0:
            days_since = 0
        remainder = days_since % every_n
        add_days = 0 if remainder == 0 else (every_n - remainder)
        candidate = base_today + timedelta(days=add_days)
        if candidate <= now_local:
            candidate += timedelta(days=every_n)
        return candidate
    return None


def get_next_cleanup_run_at(ctx: Any, scope: str = "backups") -> str:
    """Return the next scheduled cleanup run time (ISO string) for a scope."""
    ctx = as_ctx(ctx)
    cfg = _cleanup_load_config(ctx)
    scope_view = _cleanup_get_scope_view(cfg, scope)
    schedules = scope_view.get("schedules", []) if isinstance(scope_view, dict) else []
    now_local = datetime.now(ctx.DISPLAY_TZ)
    next_times: list[datetime] = []
    if isinstance(schedules, list):
        for schedule in schedules:
            if not isinstance(schedule, dict):
                continue
            candidate = _schedule_next_time(now_local, schedule)
            if candidate is not None:
                next_times.append(candidate)
    if not next_times:
        return ""
    soonest = min(next_times)
    return soonest.isoformat(timespec="seconds")
