"""Maintenance cleanup scheduler/runtime for the MC web dashboard."""
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from app.services.worker_scheduler import WorkerSpec, start_worker

from app.services.maintenance_state_store import (
    _cleanup_append_history,
    _safe_int,
    _cleanup_atomic_write_json,
    _cleanup_load_config,
    _cleanup_get_scope_view,
    _cleanup_load_history,
    _cleanup_load_non_normal,
    _cleanup_log,
    _cleanup_mark_missed_run,
    _cleanup_non_normal_path,
    _cleanup_now_iso,
    _cleanup_save_config,
    _cleanup_save_history,
    _cleanup_safe_used_percent,
)
from app.services.maintenance_policy import _cleanup_schedule_due_now
from app.services.maintenance_engine import _cleanup_run_with_lock

_cleanup_scheduler_start_lock = threading.Lock()
_cleanup_scheduler_started = False
_cleanup_runtime_last_tick = {"backups": 0, "stale_worlds": 0}


class _MappingCtx:
    def __init__(self, data):
        self._data = data if isinstance(data, dict) else {}

    def __getattr__(self, name):
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)


def _as_ctx(value):
    if hasattr(value, "ctx"):
        return value.ctx
    if isinstance(value, dict):
        return _MappingCtx(value)
    return value

def _cleanup_scheduler_loop(ctx):
    """Handle cleanup scheduler loop."""
    ctx = _as_ctx(ctx)
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
                    if schedule.get("mode") == "event":
                        event_name = str(schedule.get("event", "")).strip().lower()
                        if event_name == "server_boot" and scope not in boot_event_done:
                            result = _cleanup_run_with_lock(ctx, cfg, mode="rule", trigger=f"scheduled:{scope}:server_boot")
                            if result is None:
                                _cleanup_mark_missed_run(ctx, "lock_held", schedule_id=f"{scope}:{schedule.get('id', '')}", scope=scope)
                            else:
                                meta["last_run_at"] = _cleanup_now_iso(ctx)
                                meta["last_run_trigger"] = f"scheduled:{scope}:server_boot"
                                meta["last_run_result"] = "ok" if not result["errors"] else "partial"
                                meta["last_run_deleted"] = result["deleted_count"]
                                meta["last_run_errors"] = len(result["errors"])
                                _cleanup_append_history(
                                    ctx,
                                    trigger=f"scheduled:{scope}:server_boot",
                                    mode="rule",
                                    dry_run=False,
                                    deleted_count=result["deleted_count"],
                                    errors_count=len(result["errors"]),
                                    requested_count=result.get("requested_delete_count", 0),
                                    capped_count=result.get("capped_delete_count", result["deleted_count"]),
                                    result=meta["last_run_result"],
                                    scope=scope,
                                )
                                _cleanup_log(
                                    ctx,
                                    what="scheduled_run",
                                    why="event",
                                    trigger=f"{scope}:server_boot",
                                    result=meta["last_run_result"],
                                    details=f"deleted={result['deleted_count']};errors={len(result['errors'])}",
                                )
                                _cleanup_save_config(ctx, full_cfg)
                            boot_event_done.add(scope)
                        elif event_name == "low_free_space":
                            used_percent, _, _ = _cleanup_safe_used_percent(ctx.BACKUP_DIR)
                            threshold = _safe_int(
                                schedule.get("used_trigger_percent", cfg.get("rules", {}).get("space", {}).get("used_trigger_percent", 80)),
                                80,
                                minimum=50,
                                maximum=100,
                            )
                            if used_percent is not None and used_percent >= threshold:
                                result = _cleanup_run_with_lock(ctx, cfg, mode="rule", trigger=f"scheduled:{scope}:low_free_space")
                                if result is None:
                                    _cleanup_mark_missed_run(ctx, "lock_held", schedule_id=f"{scope}:{schedule.get('id', '')}", scope=scope)
                                else:
                                    meta["last_run_at"] = _cleanup_now_iso(ctx)
                                    meta["last_run_trigger"] = f"scheduled:{scope}:low_free_space"
                                    meta["last_run_result"] = "ok" if not result["errors"] else "partial"
                                    meta["last_run_deleted"] = result["deleted_count"]
                                    meta["last_run_errors"] = len(result["errors"])
                                    _cleanup_append_history(
                                        ctx,
                                        trigger=f"scheduled:{scope}:low_free_space",
                                        mode="rule",
                                        dry_run=False,
                                        deleted_count=result["deleted_count"],
                                        errors_count=len(result["errors"]),
                                        requested_count=result.get("requested_delete_count", 0),
                                        capped_count=result.get("capped_delete_count", result["deleted_count"]),
                                        result=meta["last_run_result"],
                                        scope=scope,
                                    )
                                    _cleanup_log(
                                        ctx,
                                        what="scheduled_run",
                                        why="event",
                                        trigger=f"{scope}:low_free_space",
                                        result=meta["last_run_result"],
                                        details=f"deleted={result['deleted_count']};errors={len(result['errors'])}",
                                    )
                                    _cleanup_save_config(ctx, full_cfg)
                        continue

                    if schedule.get("mode") == "time" and _cleanup_schedule_due_now(schedule, now_local):
                        key = f"last_schedule_run_{schedule.get('id', '')}"
                        last_at = _safe_int(meta.get(key, 0), 0, minimum=0, maximum=2_147_483_647)
                        if now_ts - last_at < 50:
                            continue
                        result = _cleanup_run_with_lock(ctx, cfg, mode="rule", trigger=f"scheduled:{scope}:{schedule.get('id', '')}")
                        if result is None:
                            _cleanup_mark_missed_run(ctx, "lock_held", schedule_id=f"{scope}:{schedule.get('id', '')}", scope=scope)
                        else:
                            meta[key] = now_ts
                            meta["last_run_at"] = _cleanup_now_iso(ctx)
                            meta["last_run_trigger"] = f"scheduled:{scope}:{schedule.get('id', '')}"
                            meta["last_run_result"] = "ok" if not result["errors"] else "partial"
                            meta["last_run_deleted"] = result["deleted_count"]
                            meta["last_run_errors"] = len(result["errors"])
                            _cleanup_append_history(
                                ctx,
                                trigger=f"scheduled:{scope}:{schedule.get('id', '')}",
                                mode="rule",
                                dry_run=False,
                                deleted_count=result["deleted_count"],
                                errors_count=len(result["errors"]),
                                requested_count=result.get("requested_delete_count", 0),
                                capped_count=result.get("capped_delete_count", result["deleted_count"]),
                                result=meta["last_run_result"],
                                scope=scope,
                            )
                            _cleanup_log(
                                ctx,
                                what="scheduled_run",
                                why="time",
                                trigger=f"{scope}:{schedule.get('id', '')}",
                                result=meta["last_run_result"],
                                details=f"deleted={result['deleted_count']};errors={len(result['errors'])}",
                            )
                        _cleanup_save_config(ctx, full_cfg)
        except Exception:
            _cleanup_mark_missed_run(ctx, "scheduler_exception")
        time.sleep(30)

def _cleanup_start_scheduler_once(ctx):
    """Handle cleanup start scheduler once."""
    ctx = _as_ctx(ctx)
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
    """Handle cleanup run event if enabled."""
    ctx = _as_ctx(ctx)
    full_cfg = _cleanup_load_config(ctx)
    for scope in ("backups", "stale_worlds"):
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        schedules = cfg.get("schedules", [])
        matched = [item for item in schedules if item.get("mode") == "event" and item.get("enabled", True) and str(item.get("event", "")).strip().lower() == event_name]
        if not matched:
            continue
        result = _cleanup_run_with_lock(ctx, cfg, mode="rule", trigger=f"event:{scope}:{event_name}")
        if result is None:
            _cleanup_mark_missed_run(ctx, "lock_held", schedule_id=f"{scope}:{event_name}", scope=scope)
            continue
        meta = cfg.setdefault("meta", {})
        meta["last_run_at"] = _cleanup_now_iso(ctx)
        meta["last_run_trigger"] = f"event:{scope}:{event_name}"
        meta["last_run_result"] = "ok" if not result["errors"] else "partial"
        meta["last_run_deleted"] = result["deleted_count"]
        meta["last_run_errors"] = len(result["errors"])
        _cleanup_append_history(
            ctx,
            trigger=f"event:{scope}:{event_name}",
            mode="rule",
            dry_run=False,
            deleted_count=result["deleted_count"],
            errors_count=len(result["errors"]),
            requested_count=result.get("requested_delete_count", 0),
            capped_count=result.get("capped_delete_count", result["deleted_count"]),
            result=meta["last_run_result"],
            scope=scope,
        )
        _cleanup_save_config(ctx, full_cfg)
        _cleanup_log(
            ctx,
            what="event_run",
            why="event_trigger",
            trigger=f"{scope}:{event_name}",
            result=meta["last_run_result"],
            details=f"deleted={result['deleted_count']};errors={len(result['errors'])}",
        )

def start_cleanup_scheduler_once(ctx):
    """Public wrapper to lazily start the maintenance scheduler."""
    return _cleanup_start_scheduler_once(_as_ctx(ctx))

def run_cleanup_event_if_enabled(ctx, event_name):
    """Public wrapper used by control routes."""
    return _cleanup_run_event_if_enabled(_as_ctx(ctx), event_name)

