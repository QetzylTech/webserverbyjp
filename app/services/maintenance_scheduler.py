"""Maintenance cleanup scheduler and event triggers."""

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.worker_scheduler import WorkerSpec, start_worker

from app.services.maintenance_context import as_ctx
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
    _safe_int,
)

_cleanup_scheduler_start_lock = threading.Lock()
_cleanup_scheduler_started = False
_cleanup_runtime_last_tick = {"backups": 0, "stale_worlds": 0}


def _save_run_result(ctx, full_cfg, cfg, *, scope, trigger, result, why, what):
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


def _run_cleanup_trigger(ctx, full_cfg, cfg, *, scope, trigger, schedule_id, why, what, extra_meta=None):
    """Run one cleanup trigger and record the outcome when work actually executes."""
    result = _cleanup_run_with_lock(ctx, cfg, mode="rule", trigger=trigger)
    if result is None:
        _cleanup_mark_missed_run(ctx, "lock_held", schedule_id=schedule_id, scope=scope)
        return False
    if extra_meta:
        cfg.setdefault("meta", {}).update(extra_meta)
    _save_run_result(ctx, full_cfg, cfg, scope=scope, trigger=trigger, result=result, why=why, what=what)
    return True


def _low_free_space_due(ctx, cfg, schedule):
    """Return whether the low-free-space event threshold is currently met."""
    used_percent, _, _ = _cleanup_safe_used_percent(ctx.BACKUP_DIR)
    threshold = _safe_int(
        schedule.get("used_trigger_percent", cfg.get("rules", {}).get("space", {}).get("used_trigger_percent", 80)),
        80,
        minimum=50,
        maximum=100,
    )
    return used_percent is not None and used_percent >= threshold


def _cleanup_scheduler_loop(ctx):
    """Poll configured schedules and run eligible cleanup jobs."""
    ctx = as_ctx(ctx)
    boot_event_done = set()
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
                last_tick = _safe_int(_cleanup_runtime_last_tick.get(scope, 0), 0, minimum=0, maximum=2_147_483_647)
                if last_tick > 0 and (now_ts - last_tick) > 75:
                    _cleanup_mark_missed_run(ctx, "scheduler_gap", schedule_id=f"{scope}:scheduler", scope=scope)
                _cleanup_runtime_last_tick[scope] = now_ts
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


def _cleanup_start_scheduler_once(ctx):
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


def _cleanup_run_event_if_enabled(ctx, event_name):
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


def start_cleanup_scheduler_once(ctx):
    """Public wrapper that lazily starts the maintenance scheduler."""
    return _cleanup_start_scheduler_once(as_ctx(ctx))



def run_cleanup_event_if_enabled(ctx, event_name):
    """Public wrapper used by control routes to fire maintenance events."""
    return _cleanup_run_event_if_enabled(as_ctx(ctx), event_name)
