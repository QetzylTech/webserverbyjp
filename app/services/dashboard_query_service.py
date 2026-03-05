"""Dashboard runtime caching, metrics, and file-list services."""
from datetime import datetime
import threading
import time
from pathlib import Path
import copy
from app.core.filesystem_utils import format_file_size
from app.core import state_store as state_store_service
from app.core import profiling
from app.ports import ports
from app.services import file_inventory_index as file_inventory_index_service
from app.services.worker_scheduler import WorkerSpec, start_worker

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
_SNAPSHOT_DIR_SIZE_CACHE_LOCK = threading.Lock()
_SNAPSHOT_DIR_SIZE_CACHE = {}


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


def _build_backup_page_items(ctx):
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
        total_size = _snapshot_dir_size_cached(path)
        ts = float(dir_stat.st_mtime)
        items.append(
            {
                "name": path.name,
                "mtime": ts,
                "size_bytes": total_size,
                "modified": datetime.fromtimestamp(ts, tz=ctx.DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z"),
                "size_text": format_file_size(total_size),
                "restore_name": f"snapshot::{path.name}",
                "download_name": f"{path.name}.zip",
                "download_url": f"/download/backups-snapshot/{path.name}",
            }
        )
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items


def invalidate_observed_state_cache(ctx=None):
    """Invalidate observed-state cache after mutating operations."""
    with _OBSERVED_STATE_CACHE_LOCK:
        _OBSERVED_STATE_CACHE["cached_at"] = 0.0
        _OBSERVED_STATE_CACHE["payload"] = None


def _is_rcon_noise_line(line):
    """Return whether a minecraft log line is known RCON shutdown/startup noise."""
    lower = (line or "").lower()
    if "thread rcon client" in lower:
        return True
    if "minecraft/rconclient" in lower and "shutting down" in lower:
        return True
    return False


def _load_minecraft_log_cache_from_latest_file(ctx, max_visible_lines=500):
    """Load recent minecraft file logs, preferring non-RCON-noise lines."""
    lines = []
    latest_path = None
    try:
        candidates = [p for p in ctx.MINECRAFT_LOGS_DIR.glob("*.log") if p.is_file()]
        if candidates:
            latest_path = max(candidates, key=lambda p: p.stat().st_mtime_ns)
    except OSError:
        latest_path = None

    if latest_path is not None:
        # Read a larger tail window so filtering still leaves enough visible lines.
        source_lines = ctx._read_recent_file_lines(latest_path, max(max_visible_lines * 8, 2000))
        filtered = [line for line in source_lines if not _is_rcon_noise_line(line)]
        lines = filtered[-max_visible_lines:]
    with ctx.minecraft_log_cache_lock:
        ctx.minecraft_log_cache_lines.clear()
        ctx.minecraft_log_cache_lines.extend(lines)
        ctx.minecraft_log_cache_loaded = True


def load_backup_log_cache_from_disk(ctx):
    """Reload backup log cache from disk into bounded in-memory storage."""
    lines = ctx._read_recent_file_lines(ctx.BACKUP_LOG_FILE, ctx.BACKUP_LOG_TEXT_LIMIT)
    mtime_ns = ctx._safe_file_mtime_ns(ctx.BACKUP_LOG_FILE)
    with ctx.backup_log_cache_lock:
        ctx.backup_log_cache_lines.clear()
        ctx.backup_log_cache_lines.extend(lines)
        ctx.backup_log_cache_loaded = True
        ctx.backup_log_cache_mtime_ns = mtime_ns


def append_backup_log_cache_line(ctx, line):
    """Append one backup log line into cache, updating file mtime hint."""
    clean = (line or "").rstrip("\r\n")
    if not clean:
        return
    with ctx.backup_log_cache_lock:
        ctx.backup_log_cache_lines.append(clean)
        ctx.backup_log_cache_loaded = True
        ctx.backup_log_cache_mtime_ns = ctx._safe_file_mtime_ns(ctx.BACKUP_LOG_FILE)


def get_cached_backup_log_text(ctx):
    """Return backup log text, reloading only when on-disk mtime changes."""
    current_mtime_ns = ctx._safe_file_mtime_ns(ctx.BACKUP_LOG_FILE)
    with ctx.backup_log_cache_lock:
        loaded = ctx.backup_log_cache_loaded
        cached_mtime_ns = ctx.backup_log_cache_mtime_ns
        if loaded and cached_mtime_ns == current_mtime_ns:
            return "\n".join(ctx.backup_log_cache_lines).strip() or "(no logs)"
    load_backup_log_cache_from_disk(ctx)
    with ctx.backup_log_cache_lock:
        return "\n".join(ctx.backup_log_cache_lines).strip() or "(no logs)"


def load_minecraft_log_cache_from_journal(ctx):
    """Prime minecraft log cache from platform-selected runtime log source."""
    output = ""
    try:
        output = str(
            ports.log.minecraft_load_recent_logs(
                ctx.SERVICE,
                ctx.MINECRAFT_LOGS_DIR,
                tail_lines=ctx.MINECRAFT_JOURNAL_TAIL_LINES,
                timeout=ctx.JOURNAL_LOAD_TIMEOUT_SECONDS,
            )
            or ""
        ).strip()
    except Exception as exc:
        if not ports.log.is_timeout_error(exc):
            ctx.log_mcweb_exception("load_minecraft_log_cache_from_journal", exc)
            output = ""
        else:
            ctx.log_mcweb_log(
                "log-load-timeout",
                command=f"minecraft_load_recent_logs service={ctx.SERVICE}",
                rejection_message=f"Timed out after {ctx.JOURNAL_LOAD_TIMEOUT_SECONDS:.1f}s.",
            )
            output = ""
    if not output:
        _load_minecraft_log_cache_from_latest_file(ctx, max_visible_lines=ctx.MINECRAFT_LOG_VISIBLE_LINES)
        return
    lines = output.splitlines()
    lines = [line for line in lines if not _is_rcon_noise_line(line)]
    if len(lines) > ctx.MINECRAFT_LOG_TEXT_LIMIT:
        lines = lines[-ctx.MINECRAFT_LOG_TEXT_LIMIT:]
    with ctx.minecraft_log_cache_lock:
        ctx.minecraft_log_cache_lines.clear()
        ctx.minecraft_log_cache_lines.extend(lines)
        ctx.minecraft_log_cache_loaded = True


def append_minecraft_log_cache_line(ctx, line):
    """Append one minecraft journal line into cache."""
    clean = (line or "").rstrip("\r\n")
    if not clean:
        return
    with ctx.minecraft_log_cache_lock:
        ctx.minecraft_log_cache_lines.append(clean)
        ctx.minecraft_log_cache_loaded = True


def get_cached_minecraft_log_text(ctx):
    """Return minecraft log cache, loading initial snapshot on demand."""
    with ctx.minecraft_log_cache_lock:
        if ctx.minecraft_log_cache_loaded:
            return "\n".join(ctx.minecraft_log_cache_lines).strip() or "(no logs)"
    load_minecraft_log_cache_from_journal(ctx)
    with ctx.minecraft_log_cache_lock:
        return "\n".join(ctx.minecraft_log_cache_lines).strip() or "(no logs)"


def load_mcweb_log_cache_from_disk(ctx):
    """Reload mcweb action log cache from disk."""
    lines = ctx._read_recent_file_lines(ctx.MCWEB_ACTION_LOG_FILE, ctx.MCWEB_ACTION_LOG_TEXT_LIMIT)
    mtime_ns = ctx._safe_file_mtime_ns(ctx.MCWEB_ACTION_LOG_FILE)
    with ctx.mcweb_log_cache_lock:
        ctx.mcweb_log_cache_lines.clear()
        ctx.mcweb_log_cache_lines.extend(lines)
        ctx.mcweb_log_cache_loaded = True
        ctx.mcweb_log_cache_mtime_ns = mtime_ns


def append_mcweb_log_cache_line(ctx, line):
    """Append one mcweb action log line into cache."""
    clean = (line or "").rstrip("\r\n")
    if not clean:
        return
    with ctx.mcweb_log_cache_lock:
        ctx.mcweb_log_cache_lines.append(clean)
        ctx.mcweb_log_cache_loaded = True
        ctx.mcweb_log_cache_mtime_ns = ctx._safe_file_mtime_ns(ctx.MCWEB_ACTION_LOG_FILE)


def get_cached_mcweb_log_text(ctx):
    """Return mcweb action log text, refreshing if file changed."""
    current_mtime_ns = ctx._safe_file_mtime_ns(ctx.MCWEB_ACTION_LOG_FILE)
    with ctx.mcweb_log_cache_lock:
        loaded = ctx.mcweb_log_cache_loaded
        cached_mtime_ns = ctx.mcweb_log_cache_mtime_ns
        if loaded and cached_mtime_ns == current_mtime_ns:
            return "\n".join(ctx.mcweb_log_cache_lines).strip() or "(no logs)"
    load_mcweb_log_cache_from_disk(ctx)
    with ctx.mcweb_log_cache_lock:
        return "\n".join(ctx.mcweb_log_cache_lines).strip() or "(no logs)"


def set_file_page_items(ctx, cache_key, items):
    """Replace cached file-list payload for one page section."""
    with ctx.file_page_cache_lock:
        ctx.file_page_cache[cache_key] = {
            "items": [dict(item) for item in items],
            "updated_at": time.time(),
        }


def refresh_file_page_items(ctx, cache_key):
    """Refresh one file-list cache key from its backing directory."""
    if cache_key == "backups":
        items = _build_backup_page_items(ctx)
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


def mark_file_page_client_active(ctx):
    """Record recent file-page activity for refresher throttling."""
    with ctx.file_page_cache_lock:
        ctx.file_page_last_seen = time.time()


def has_active_file_page_clients(ctx):
    """Return whether file-page clients are still considered active."""
    with ctx.file_page_cache_lock:
        last_seen = ctx.file_page_last_seen
    return (time.time() - last_seen) <= ctx.FILE_PAGE_ACTIVE_TTL_SECONDS


def get_cached_file_page_items(ctx, cache_key):
    """Return cached file-list items when fresh; otherwise refresh."""
    with ctx.file_page_cache_lock:
        entry = ctx.file_page_cache.get(cache_key)
        if entry:
            age = time.time() - entry["updated_at"]
            if entry["items"] and age <= ctx.FILE_PAGE_CACHE_REFRESH_SECONDS:
                return [dict(item) for item in entry["items"]]
    return refresh_file_page_items(ctx, cache_key)


def file_page_cache_refresher_loop(ctx):
    """Background refresher that updates file lists only when viewed."""
    while True:
        if has_active_file_page_clients(ctx):
            for cache_key in ("backups", "crash_logs", "minecraft_logs"):
                try:
                    refresh_file_page_items(ctx, cache_key)
                except Exception as exc:
                    ctx.log_mcweb_exception(f"file_page_cache_refresh/{cache_key}", exc)
            time.sleep(ctx.FILE_PAGE_CACHE_REFRESH_SECONDS)
        else:
            time.sleep(1)


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


def get_backups_status(ctx):
    """Return backup directory health and current zip count summary."""
    if not ctx.BACKUP_DIR.exists() or not ctx.BACKUP_DIR.is_dir():
        return "missing"
    zip_count = sum(1 for _ in ctx.BACKUP_DIR.glob("*.zip"))
    return f"ready ({zip_count} zip files)"


def get_observed_state(ctx):
    """Return runtime-observed snapshot from service/filesystem and latest operations."""
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

    def _active(op):
        if not isinstance(op, dict):
            return False
        return str(op.get("status", "")).strip().lower() in {"intent", "in_progress"}

    # Boot/runtime precedence: if probe already sees the service as active,
    # report Running immediately and ignore stale async intent rows.
    if service_status_raw != "active":
        if _active(latest_restore):
            service_status_raw = "shutting_down"
        elif _active(latest_stop):
            service_status_raw = "shutting_down"
        elif _active(latest_start):
            service_status_raw = "starting"
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


def _operation_age_seconds(op, now_epoch):
    if not isinstance(op, dict):
        return 0.0
    started = str(op.get("started_at", "") or "").strip()
    intent = str(op.get("intent_at", "") or "").strip()
    source = started or intent
    if not source:
        return 0.0
    try:
        ts = datetime.fromisoformat(source.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0
    return max(0.0, now_epoch - ts)


def get_consistency_report(ctx, *, auto_repair=False):
    """Validate runtime invariants and optionally repair safe drift."""
    with profiling.timed("consistency.report"):
        issues = []
        repairs = []
        service_status = str(ctx.get_status() or "").strip().lower()
        try:
            session_start = ctx.read_session_start_time()
        except Exception:
            session_start = None

        if service_status not in ctx.OFF_STATES and session_start is None:
            issue = {
                "code": "active_missing_session_start",
                "message": "Service is active but session start timestamp is missing.",
                "severity": "warning",
            }
            issues.append(issue)
            if auto_repair:
                try:
                    repaired = ctx.write_session_start_time() is not None
                except Exception:
                    repaired = False
                repairs.append({
                    "code": "write_session_start",
                    "ok": bool(repaired),
                    "message": "Attempted to restore missing session timestamp.",
                })

        if service_status in ctx.OFF_STATES and session_start is not None:
            issue = {
                "code": "off_with_session_start",
                "message": "Service is off but session start timestamp still exists.",
                "severity": "warning",
            }
            issues.append(issue)
            if auto_repair:
                try:
                    ctx.clear_session_start_time()
                    repaired = True
                except Exception:
                    repaired = False
                repairs.append({
                    "code": "clear_session_start",
                    "ok": bool(repaired),
                    "message": "Attempted to clear stale session timestamp.",
                })

        return {
            "ok": len(issues) == 0,
            "service_status_raw": service_status,
            "issues": issues,
            "repairs": repairs,
            "checked_at": datetime.now().isoformat(),
        }


def reconcile_operations_once(ctx):
    """Advance stale/finished async operations using observed runtime state."""
    with profiling.timed("reconciler.iteration"):
        db_path = Path(ctx.APP_STATE_DB_PATH)
        with profiling.timed("reconciler.fetch_active_ops"):
            active_ops = state_store_service.list_operations_by_status(
                db_path,
                statuses=("intent", "in_progress"),
                limit=200,
            )
        profiling.set_gauge("reconciler.active_ops", len(active_ops))
        if not active_ops:
            with profiling.timed("reconciler.consistency_check"):
                try:
                    get_consistency_report(ctx, auto_repair=True)
                except Exception as exc:
                    ctx.log_mcweb_exception("reconcile_consistency_report", exc)
            return 0
        updated = 0
        now_epoch = time.time()
        service_status = str(ctx.get_status() or "").strip().lower()
        pending_updates = []

        def _queue_update(op_id, **kwargs):
            if not str(op_id or "").strip():
                return
            payload = {"op_id": str(op_id)}
            payload.update(kwargs)
            pending_updates.append(payload)

        for op in active_ops:
            with profiling.timed("reconciler.per_operation"):
                op_id = str(op.get("op_id", "") or "")
                op_type = str(op.get("op_type", "") or "").strip().lower()
                status = str(op.get("status", "") or "").strip().lower()
                age = _operation_age_seconds(op, now_epoch)
                data = op.get("data", {}) if isinstance(op.get("data"), dict) else {}

                if op_type == "start":
                    if service_status == "active":
                        _queue_update(
                            op_id,
                            status="observed",
                            message="Service start observed by reconciler.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if status == "intent" and age >= float(ctx.OPERATION_INTENT_STALE_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="intent_stale",
                            message="Start operation stale before worker progress.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if age >= float(ctx.OPERATION_START_TIMEOUT_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="start_timeout",
                            message="Start operation timed out.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    continue

                if op_type == "restore":
                    restore_job_id = str(data.get("restore_job_id", "") or "").strip()
                    if restore_job_id:
                        payload = ctx.get_restore_status(since_seq=0, job_id=restore_job_id)
                        if isinstance(payload, dict) and not bool(payload.get("running")):
                            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                            if bool(result.get("ok")):
                                _queue_update(
                                    op_id,
                                    status="observed",
                                    message=str(result.get("message", "Restore observed complete.") or "Restore observed complete."),
                                    payload={"restore_job_id": restore_job_id, "result": result},
                                    finished=True,
                                )
                            else:
                                _queue_update(
                                    op_id,
                                    status="failed",
                                    error_code=str(result.get("error", "") or "restore_failed"),
                                    message=str(result.get("message", "Restore failed.") or "Restore failed."),
                                    payload={"restore_job_id": restore_job_id, "result": result},
                                    finished=True,
                                )
                            updated += 1
                            continue
                    if status == "intent" and age >= float(ctx.OPERATION_INTENT_STALE_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="intent_stale",
                            message="Restore operation stale before worker progress.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if age >= float(ctx.OPERATION_RESTORE_TIMEOUT_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="restore_timeout",
                            message="Restore operation timed out.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    continue

                if op_type == "stop":
                    if service_status in ctx.OFF_STATES:
                        _queue_update(
                            op_id,
                            status="observed",
                            message="Service stop observed by reconciler.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if age >= float(ctx.OPERATION_STOP_TIMEOUT_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="stop_timeout",
                            message="Stop operation timed out.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    continue

                if op_type == "backup":
                    backup_status, _backup_class = ctx.get_backup_status()
                    backup_running = str(backup_status or "").strip().lower() == "running"
                    backup_timeout_seconds = float(
                        getattr(
                            ctx,
                            "OPERATION_BACKUP_TIMEOUT_SECONDS",
                            max(float(ctx.OPERATION_STOP_TIMEOUT_SECONDS), 900.0),
                        )
                    )
                    if status == "intent" and age >= float(ctx.OPERATION_INTENT_STALE_SECONDS):
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="intent_stale",
                            message="Backup operation stale before worker progress.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    if status == "in_progress" and not backup_running and age >= backup_timeout_seconds:
                        _queue_update(
                            op_id,
                            status="failed",
                            error_code="backup_timeout",
                            message="Backup operation timed out.",
                            finished=True,
                        )
                        updated += 1
                        continue
                    continue

        if pending_updates:
            state_store_service.update_operations_batch(db_path, updates=pending_updates)

        with profiling.timed("reconciler.consistency_check"):
            try:
                consistency = get_consistency_report(ctx, auto_repair=True)
            except Exception as exc:
                ctx.log_mcweb_exception("reconcile_consistency_report", exc)
                consistency = {"ok": True, "issues": []}
            if not bool(consistency.get("ok")):
                try:
                    ctx.log_mcweb_log(
                        "consistency-warning",
                        command="runtime_invariants",
                        rejection_message=str(consistency.get("issues", []))[:700],
                    )
                except Exception:
                    pass

        return updated


def operation_reconciler_loop(ctx):
    """Background reconciliation loop for async operation states."""
    while True:
        try:
            reconcile_operations_once(ctx)
        except Exception as exc:
            ctx.log_mcweb_exception("operation_reconciler_loop", exc)
        time.sleep(float(ctx.OPERATION_RECONCILE_INTERVAL_SECONDS))


def start_operation_reconciler(ctx):
    """Start operation reconciler daemon thread once."""
    if ctx.operation_reconciler_started:
        return
    with ctx.operation_reconciler_start_lock:
        if ctx.operation_reconciler_started:
            return
        start_worker(
            ctx,
            WorkerSpec(
                name="operation-reconciler",
                target=operation_reconciler_loop,
                args=(ctx,),
                interval_source=getattr(ctx, "OPERATION_RECONCILE_INTERVAL_SECONDS", None),
                stop_signal_name="operation_reconciler_stop_event",
                health_marker="operation_reconciler",
            ),
        )
        ctx.operation_reconciler_started = True


