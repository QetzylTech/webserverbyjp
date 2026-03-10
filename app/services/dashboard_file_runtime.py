"""Dashboard file-page cache runtime helpers."""
from datetime import datetime
from pathlib import Path
import threading
import time

from app.core.filesystem_utils import format_file_size
from app.core import state_store as state_store_service
from app.services import file_inventory_index as file_inventory_index_service
from app.services.worker_scheduler import WorkerSpec, start_worker

_SNAPSHOT_DIR_SIZE_CACHE_LOCK = threading.Lock()
_SNAPSHOT_DIR_SIZE_CACHE = {}


def _safe_dir_mtime_ns(path):
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return -1


def _snapshot_dir_size_cached(path):
    """Return recursive directory size with mtime-based cache reuse."""
    key = str(path.resolve())
    mtime_ns = _safe_dir_mtime_ns(path)
    with _SNAPSHOT_DIR_SIZE_CACHE_LOCK:
        cached = _SNAPSHOT_DIR_SIZE_CACHE.get(key)
        if isinstance(cached, dict) and int(cached.get("mtime_ns", -1)) == mtime_ns:
            return int(cached.get("size", 0))
    total_size = 0
    try:
        for child in path.rglob("*"):
            if not child.is_file():
                continue
            try:
                total_size += int(child.stat().st_size)
            except OSError:
                continue
    except OSError:
        total_size = 0
    with _SNAPSHOT_DIR_SIZE_CACHE_LOCK:
        _SNAPSHOT_DIR_SIZE_CACHE[key] = {"mtime_ns": mtime_ns, "size": int(total_size)}
    return int(total_size)


def _previous_file_page_items(ctx, cache_key):
    with ctx.file_page_cache_lock:
        entry = ctx.file_page_cache.get(cache_key) or {}
        items = entry.get("items") if isinstance(entry, dict) else []
        if isinstance(items, list) and items:
            return [dict(item) for item in items if isinstance(item, dict)]
    try:
        return state_store_service.load_file_records_snapshot(Path(ctx.APP_STATE_DB_PATH), source_key=cache_key)
    except Exception:
        return []


def _build_backup_page_items(ctx, *, compute_snapshot_sizes=True, previous_items=None):
    """Build backup list items (zip backups + snapshot dirs) with mtime index cache."""
    backup_dir = Path(ctx.BACKUP_DIR)
    snapshot_root = Path(getattr(ctx, "AUTO_SNAPSHOT_DIR", "") or (backup_dir / "snapshots"))
    session_state = getattr(ctx, "session_state", None)
    session_file_text = str(getattr(session_state, "session_file", "") or "").strip() if session_state is not None else ""
    session_file = Path(session_file_text) if session_file_text else None
    old_worlds_root = (session_file.parent / "old_worlds").resolve() if session_file is not None else Path("__unused_old_worlds_index_root__")
    inventory = file_inventory_index_service.get_inventory(
        backup_root=backup_dir,
        snapshot_root=snapshot_root,
        old_worlds_root=old_worlds_root,
    )

    previous_by_name = {}
    if isinstance(previous_items, list):
        previous_by_name = {
            str(item.get("name", "") or ""): dict(item)
            for item in previous_items
            if isinstance(item, dict) and str(item.get("name", "") or "").strip()
        }

    items = []
    for path in inventory.get("backup_zip_paths", []):
        try:
            stat = path.stat()
        except OSError:
            continue
        ts = float(stat.st_mtime)
        size_bytes = int(stat.st_size)
        items.append(
            {
                "name": path.name,
                "mtime": ts,
                "size_bytes": size_bytes,
                "modified": datetime.fromtimestamp(ts, tz=ctx.DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z"),
                "size_text": format_file_size(size_bytes),
                "restore_name": path.name,
                "download_name": path.name,
                "download_url": f"/download/backups/{path.name}",
            }
        )
    for path in inventory.get("snapshot_dir_paths", []):
        if not path.is_dir():
            continue
        try:
            dir_stat = path.stat()
        except OSError:
            continue
        previous_item = previous_by_name.get(path.name, {})
        if compute_snapshot_sizes:
            total_size = _snapshot_dir_size_cached(path)
            size_text = format_file_size(total_size)
        else:
            previous_size = previous_item.get("size_bytes") if isinstance(previous_item, dict) else None
            if isinstance(previous_size, int) and previous_size >= 0:
                total_size = previous_size
                size_text = str(previous_item.get("size_text", "") or format_file_size(total_size))
            else:
                total_size = -1
                size_text = "Calculating..."
        ts = float(dir_stat.st_mtime)
        items.append(
            {
                "name": path.name,
                "mtime": ts,
                "size_bytes": total_size,
                "modified": datetime.fromtimestamp(ts, tz=ctx.DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z"),
                "size_text": size_text,
                "restore_name": f"snapshot::{path.name}",
                "download_name": f"{path.name}.zip",
                "download_url": f"/download/backups-snapshot/{path.name}",
            }
        )
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items


def mark_file_page_client_active(ctx):
    """Record recent file-page activity and wake cadence workers."""
    with ctx.metrics_cache_cond:
        ctx.file_page_last_seen = time.time()
        ctx.metrics_cache_cond.notify_all()


def has_active_file_page_clients(ctx):
    """Return whether file-page activity is still within the active TTL."""
    with ctx.metrics_cache_cond:
        last_seen = float(getattr(ctx, "file_page_last_seen", 0.0) or 0.0)
        ttl_seconds = float(getattr(ctx, "FILE_PAGE_ACTIVE_TTL_SECONDS", 0.0) or 0.0)
    return (time.time() - last_seen) <= ttl_seconds


def set_file_page_items(ctx, cache_key, items):
    """Replace cached file-list payload for one page section."""
    with ctx.file_page_cache_lock:
        ctx.file_page_cache[cache_key] = {
            "items": [dict(item) for item in items],
            "updated_at": time.time(),
        }


def refresh_file_page_items(ctx, cache_key, *, compute_snapshot_sizes=True):
    """Refresh one file-list cache key from its backing directory."""
    if cache_key == "backups":
        items = _build_backup_page_items(
            ctx,
            compute_snapshot_sizes=bool(compute_snapshot_sizes),
            previous_items=_previous_file_page_items(ctx, cache_key),
        )
    elif cache_key == "crash_logs":
        items = ctx._list_download_files(ctx.CRASH_REPORTS_DIR, "*.txt", ctx.DISPLAY_TZ)
    elif cache_key == "minecraft_logs":
        items = ctx._list_download_files(ctx.MINECRAFT_LOGS_DIR, "*.log", ctx.DISPLAY_TZ)
        items.extend(ctx._list_download_files(ctx.MINECRAFT_LOGS_DIR, "*.gz", ctx.DISPLAY_TZ))
        items.sort(key=lambda item: item["mtime"], reverse=True)
    else:
        return []
    try:
        state_store_service.replace_file_records_snapshot(
            Path(ctx.APP_STATE_DB_PATH),
            source_key=cache_key,
            items=items,
        )
    except Exception as exc:
        ctx.log_mcweb_exception(f"file_records_sync/{cache_key}", exc)
    set_file_page_items(ctx, cache_key, items)
    return items


def get_cached_file_page_items(ctx, cache_key):
    """Return cached file-list items when fresh; otherwise load DB snapshot or refresh lazily."""
    with ctx.file_page_cache_lock:
        entry = ctx.file_page_cache.get(cache_key)
        if entry:
            age = time.time() - entry["updated_at"]
            if entry["items"] and age <= ctx.FILE_PAGE_CACHE_REFRESH_SECONDS:
                return [dict(item) for item in entry["items"]]
    try:
        persisted = state_store_service.load_file_records_snapshot(Path(ctx.APP_STATE_DB_PATH), source_key=cache_key)
    except Exception:
        persisted = []
    if persisted:
        set_file_page_items(ctx, cache_key, persisted)
        return [dict(item) for item in persisted]
    return refresh_file_page_items(ctx, cache_key, compute_snapshot_sizes=False)


def warm_file_page_caches(ctx):
    """Warm file-page caches at startup without blocking on snapshot size scans."""
    for cache_key in ("backups", "crash_logs", "minecraft_logs"):
        refresh_file_page_items(ctx, cache_key, compute_snapshot_sizes=False)


def file_page_cache_refresher_loop(ctx):
    """Background refresher that updates file lists only when viewed."""
    while True:
        service_status = str(ctx.get_status() or "inactive").strip().lower()
        off_states = {str(item or "").strip().lower() for item in getattr(ctx, "OFF_STATES", {"inactive", "failed"})}
        if has_active_file_page_clients(ctx):
            for cache_key in ("backups", "crash_logs", "minecraft_logs"):
                try:
                    refresh_file_page_items(ctx, cache_key)
                except Exception as exc:
                    ctx.log_mcweb_exception(f"file_page_cache_refresh/{cache_key}", exc)
            interval = ctx.FILE_PAGE_CACHE_REFRESH_SECONDS if service_status not in off_states else max(
                float(ctx.FILE_PAGE_CACHE_REFRESH_SECONDS),
                float(getattr(ctx, "SLOW_METRICS_INTERVAL_OFF_SECONDS", ctx.FILE_PAGE_CACHE_REFRESH_SECONDS)),
            )
            time.sleep(interval)
        else:
            idle_sleep = max(
                float(getattr(ctx, "SLOW_METRICS_INTERVAL_OFF_SECONDS", 15.0)),
                float(ctx.FILE_PAGE_CACHE_REFRESH_SECONDS),
            ) if service_status in off_states else 5.0
            time.sleep(idle_sleep)


def ensure_file_page_cache_refresher_started(ctx):
    """Start file-page refresher daemon once."""
    if ctx.file_page_cache_refresher_started:
        return
    with ctx.file_page_cache_refresher_start_lock:
        if ctx.file_page_cache_refresher_started:
            return
        start_worker(
            ctx,
            WorkerSpec(
                name="file-page-cache-refresher",
                target=file_page_cache_refresher_loop,
                args=(ctx,),
                interval_source=getattr(ctx, "FILE_PAGE_CACHE_REFRESH_SECONDS", None),
                stop_signal_name="file_page_cache_refresher_stop_event",
                health_marker="file_page_cache_refresher",
            ),
        )
        ctx.file_page_cache_refresher_started = True
