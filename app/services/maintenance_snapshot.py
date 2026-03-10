"""Maintenance state snapshot helpers."""

from datetime import datetime, timedelta

from app.core import profiling
from app.services.maintenance_context import as_ctx
from app.services.maintenance_state_store import (
    _cleanup_load_history,
    _cleanup_load_non_normal,
    _cleanup_safe_used_percent,
    _safe_int,
)


def _cleanup_state_snapshot(ctx, cfg):
    """Return a compact snapshot of cleanup state for UI polling."""
    ctx = as_ctx(ctx)
    with profiling.timed("maintenance.state_snapshot.total"):
        def _next_time_schedule_run():
            tz = getattr(ctx, "DISPLAY_TZ", None)
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

        used_percent, total_bytes, free_bytes = _cleanup_safe_used_percent(ctx.BACKUP_DIR)
        with profiling.timed("maintenance.state_snapshot.non_normal_load"):
            non_normal = _cleanup_load_non_normal(ctx)
        with profiling.timed("maintenance.state_snapshot.history_load"):
            history = _cleanup_load_history(ctx)
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
