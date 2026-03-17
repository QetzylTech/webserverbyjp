"""Dashboard observed-state caching helpers."""
import copy
from pathlib import Path
import threading
import time

from app.core import profiling
from app.core import state_store as state_store_service

_OBSERVED_OPS_CACHE_LOCK = threading.Lock()
_OBSERVED_OPS_CACHE = {
    "db_path": "",
    "cached_at": 0.0,
    "latest_start": None,
    "latest_stop": None,
    "latest_restore": None,
}
_OBSERVED_OPS_CACHE_TTL_SECONDS = 1.5
_OBSERVED_STATE_CACHE_LOCK = threading.Lock()
_OBSERVED_STATE_CACHE_TTL_SECONDS = 1.25
_OBSERVED_STATE_CACHE = {
    "cached_at": 0.0,
    "payload": None,
}


def _get_cached_latest_operations(db_path):
    """Return cached latest operation rows for start/stop/restore within a short TTL."""
    now = time.time()
    key = str(db_path)
    with _OBSERVED_OPS_CACHE_LOCK:
        is_fresh = (_OBSERVED_OPS_CACHE["cached_at"] + _OBSERVED_OPS_CACHE_TTL_SECONDS) >= now
        if _OBSERVED_OPS_CACHE["db_path"] == key and is_fresh:
            return (
                _OBSERVED_OPS_CACHE["latest_start"],
                _OBSERVED_OPS_CACHE["latest_stop"],
                _OBSERVED_OPS_CACHE["latest_restore"],
            )
    with profiling.timed("observed_state.operation_aggregation"):
        latest_start = state_store_service.get_latest_operation_for_type(db_path, "start")
        latest_stop = state_store_service.get_latest_operation_for_type(db_path, "stop")
        latest_restore = state_store_service.get_latest_operation_for_type(db_path, "restore")
    with _OBSERVED_OPS_CACHE_LOCK:
        _OBSERVED_OPS_CACHE["db_path"] = key
        _OBSERVED_OPS_CACHE["cached_at"] = now
        _OBSERVED_OPS_CACHE["latest_start"] = latest_start
        _OBSERVED_OPS_CACHE["latest_stop"] = latest_stop
        _OBSERVED_OPS_CACHE["latest_restore"] = latest_restore
    return latest_start, latest_stop, latest_restore


def invalidate_observed_state_cache(ctx=None):
    """Invalidate observed-state cache after mutating operations."""
    with _OBSERVED_STATE_CACHE_LOCK:
        _OBSERVED_STATE_CACHE["cached_at"] = 0.0
        _OBSERVED_STATE_CACHE["payload"] = None


def get_backups_status(ctx):
    """Return backup directory health and current zip count summary."""
    if not ctx.BACKUP_DIR.exists() or not ctx.BACKUP_DIR.is_dir():
        return "missing"
    zip_count = sum(1 for _ in ctx.BACKUP_DIR.glob("*.zip"))
    return f"ready ({zip_count} zip files)"


def _active_operation(op):
    if not isinstance(op, dict):
        return False
    return str(op.get("status", "") or "").strip().lower() in {"intent", "in_progress"}


def _transition_intent(ctx):
    getter = getattr(ctx, "get_service_status_intent", None)
    if not callable(getter):
        return ""
    try:
        return str(getter() or "").strip().lower()
    except Exception:
        return ""


def _resolve_observed_service_status(ctx, service_status_raw, *, latest_start, latest_stop, latest_restore):
    raw = str(service_status_raw or "inactive").strip().lower()
    off_states = {str(item or "").strip().lower() for item in getattr(ctx, "OFF_STATES", {"inactive", "failed"})}
    if raw == "active" or raw in off_states:
        return raw
    if _active_operation(latest_restore) or _active_operation(latest_stop):
        return "shutting_down"
    if _active_operation(latest_start):
        return "starting"

    intent = _transition_intent(ctx)
    if intent == "shutting":
        return "shutting_down"
    if intent == "starting":
        return "starting"
    return raw


def get_observed_state(ctx):
    """Return runtime-observed snapshot from service/filesystem and latest operations.

    This read path is intentionally cached. That reduces repeated DB and probe work, but it
    also means start/stop intent may take about 1 second to appear on the dashboard even when
    the control route has already recorded the intent and published fresh metrics.
    """
    now = time.time()
    with _OBSERVED_STATE_CACHE_LOCK:
        cached_at = float(_OBSERVED_STATE_CACHE.get("cached_at", 0.0) or 0.0)
        payload = _OBSERVED_STATE_CACHE.get("payload")
        if isinstance(payload, dict) and (now - cached_at) <= _OBSERVED_STATE_CACHE_TTL_SECONDS:
            return copy.deepcopy(payload)

    with profiling.timed("observed_state.total"):
        with profiling.timed("observed_state.service_probe"):
            service_status_raw = str(ctx.get_status() or "inactive").strip().lower()
        with profiling.timed("observed_state.filesystem_checks"):
            world_dir = Path(getattr(ctx, "WORLD_DIR", ""))
            backup_dir = Path(getattr(ctx, "BACKUP_DIR", ""))
            snapshot_dir = Path(getattr(ctx, "AUTO_SNAPSHOT_DIR", "") or (backup_dir / "snapshots"))
        latest_start = None
        latest_stop = None
        latest_restore = None
        try:
            db_path = Path(ctx.APP_STATE_DB_PATH)
            latest_start, latest_stop, latest_restore = _get_cached_latest_operations(db_path)
        except Exception:
            latest_start = None
            latest_stop = None
            latest_restore = None

    # Boot/runtime precedence: if probe already sees the service as active,
    # report Running immediately and ignore stale async intent rows. When the probe is
    # still off/inactive, prefer active operation rows and then in-memory transition
    # intent so the dashboard can show Starting or Shutting Down immediately.
    service_status_raw = _resolve_observed_service_status(
        ctx,
        service_status_raw,
        latest_start=latest_start,
        latest_stop=latest_stop,
        latest_restore=latest_restore,
    )
    players_online = ctx.get_players_online()
    service_status_display = ctx.get_service_status_display(service_status_raw, players_online)
    observed_payload = {
        "service_status_raw": service_status_raw,
        "service_status_display": service_status_display,
        "service_status_class": ctx.get_service_status_class(service_status_display),
        "players_online": players_online,
        "world_dir_exists": bool(world_dir.exists() and world_dir.is_dir()) if str(world_dir) else False,
        "backup_dir_exists": bool(backup_dir.exists() and backup_dir.is_dir()) if str(backup_dir) else False,
        "snapshot_dir_exists": bool(snapshot_dir.exists() and snapshot_dir.is_dir()) if str(snapshot_dir) else False,
        "latest_start_operation": latest_start if isinstance(latest_start, dict) else {},
        "latest_stop_operation": latest_stop if isinstance(latest_stop, dict) else {},
        "latest_restore_operation": latest_restore if isinstance(latest_restore, dict) else {},
    }
    with _OBSERVED_STATE_CACHE_LOCK:
        _OBSERVED_STATE_CACHE["cached_at"] = now
        _OBSERVED_STATE_CACHE["payload"] = copy.deepcopy(observed_payload)
    return observed_payload
