"""Backup control-plane use cases and scheduling helpers."""

from datetime import datetime
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any, Callable, Iterator

from app.core import state_store as state_store_service
from app.ports import ports
from app.services.restore_workflow_helpers import is_backup_running

_calls = SimpleNamespace(
    run_backup_script=ports.backup.run_backup_script,
)


def _backup_snapshot_root(ctx: Any) -> Path:
    auto_snapshot_dir = getattr(ctx, "AUTO_SNAPSHOT_DIR", "")
    if isinstance(auto_snapshot_dir, Path) and str(auto_snapshot_dir):
        return auto_snapshot_dir
    if str(auto_snapshot_dir or "").strip():
        return Path(str(auto_snapshot_dir))
    return Path(ctx.BACKUP_DIR) / "snapshots"


def _iter_backup_artifacts(ctx: Any) -> Iterator[Path]:
    backup_dir = Path(ctx.BACKUP_DIR)
    if backup_dir.exists() and backup_dir.is_dir():
        yield from backup_dir.glob("*.zip")

    snapshot_root = _backup_snapshot_root(ctx)
    if snapshot_root.exists() and snapshot_root.is_dir():
        for path in snapshot_root.iterdir():
            if path.is_dir():
                yield path


def _scan_backup_artifacts(ctx: Any, *, stat_attr: str) -> dict[str, float]:
    snapshot: dict[str, float] = {}
    for path in _iter_backup_artifacts(ctx):
        try:
            snapshot[str(path)] = float(getattr(path.stat(), stat_attr))
        except OSError:
            continue
    return snapshot


def run_backup_script(
    ctx: Any,
    count_skip_as_success: bool = True,
    trigger: str = "manual",
    *,
    snapshot_reader: Callable[[Any], dict[str, float]] | None = None,
    snapshot_changed_fn: Callable[[Any, dict[str, float], dict[str, float]], bool] | None = None,
) -> bool:
    """Run backup script with single-flight locking and snapshot verification."""
    backup_state = ctx.backup_state
    read_snapshot = snapshot_reader or get_backup_zip_snapshot
    has_snapshot_changed = snapshot_changed_fn or backup_snapshot_changed
    if not backup_state.run_lock.acquire(blocking=False):
        return bool(count_skip_as_success)
    try:
        if is_backup_running(ctx, include_run_lock=False):
            with backup_state.lock:
                backup_state.last_error = ""
            return bool(count_skip_as_success)

        with backup_state.lock:
            backup_state.last_error = ""

        before_snapshot = read_snapshot(ctx)
        try:
            direct_result = _calls.run_backup_script(ctx.BACKUP_SCRIPT, trigger, timeout=600)
        except OSError as exc:
            message = f"Backup script execution failed: {exc}"
            with backup_state.lock:
                backup_state.last_error = message[:700]
            try:
                ctx.log_mcweb_exception("run_backup_script", exc)
            except Exception:
                pass
            return False
        except Exception as exc:
            if not ports.backup.is_timeout_error(exc):
                raise
            message = "Backup timed out after 600s."
            with backup_state.lock:
                backup_state.last_error = message
            ctx.log_mcweb_log(
                "backup-timeout",
                command=f"trigger={trigger}",
                rejection_message=message,
            )
            return False

        after_direct_snapshot = read_snapshot(ctx)
        direct_created_zip = has_snapshot_changed(ctx, before_snapshot, after_direct_snapshot)
        if direct_result.returncode == 0:
            return True
        if direct_created_zip:
            detail = ((direct_result.stderr or "") + "\n" + (direct_result.stdout or "")).strip()
            message = f"Backup completed with warnings (trigger={trigger})."
            if detail:
                message = f"{message} {detail[:400]}"
            ctx.set_backup_warning(message)
            ctx.log_mcweb_action("backup-warning", command=f"trigger={trigger}", rejection_message=message[:700])
            return True

        err = ((direct_result.stderr or "") + "\n" + (direct_result.stdout or "")).strip()
        if not err:
            try:
                tail_lines = ports.filesystem.read_text(ctx.BACKUP_LOG_FILE, encoding="utf-8", errors="ignore").splitlines()
                if tail_lines:
                    err = " | ".join(tail_lines[-3:]).strip()
            except Exception:
                err = ""
        with backup_state.lock:
            backup_state.last_error = err[:700] if err else "Backup command returned non-zero exit status."
        return False
    finally:
        backup_state.run_lock.release()


def format_backup_time(ctx: Any, timestamp: float | None) -> str:
    """Format epoch seconds in configured display timezone."""
    if timestamp is None:
        return "--"
    return datetime.fromtimestamp(timestamp, tz=ctx.DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z")


def get_server_time_text(ctx: Any) -> str:
    """Return current server time in configured display timezone."""
    return datetime.now(tz=ctx.DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z")


def get_latest_backup_zip_timestamp(ctx: Any) -> float | None:
    """Return latest backup artifact timestamp across zips and snapshots."""
    latest = None
    for ts in _scan_backup_artifacts(ctx, stat_attr="st_mtime").values():
        if latest is None or ts > latest:
            latest = ts
    return latest


def get_backup_zip_snapshot(ctx: Any) -> dict[str, float]:
    """Capture backup artifact mtime snapshot for output verification."""
    return _scan_backup_artifacts(ctx, stat_attr="st_mtime_ns")


def backup_snapshot_changed(ctx: Any, before_snapshot: dict[str, float], after_snapshot: dict[str, float]) -> bool:
    """Return True when any backup artifact was created or modified."""
    if not before_snapshot and after_snapshot:
        return True
    for file_path, after_mtime in after_snapshot.items():
        before_mtime = before_snapshot.get(file_path)
        if before_mtime is None or after_mtime != before_mtime:
            return True
    return False


def get_backup_schedule_times(ctx: Any, service_status: object = None) -> dict[str, str]:
    """Return formatted last/next backup schedule timestamps."""
    if service_status is None:
        service_status = ctx.get_status()

    latest_zip_ts = get_latest_backup_zip_timestamp(ctx)
    last_backup_ts = latest_zip_ts
    next_backup_at = None
    if service_status not in ctx.OFF_STATES:
        session_start = ctx.get_session_start_time(service_status)
        if session_start is not None:
            elapsed_intervals = int(max(0, time.time() - session_start) // ctx.BACKUP_INTERVAL_SECONDS)
            next_backup_at = session_start + ((elapsed_intervals + 1) * ctx.BACKUP_INTERVAL_SECONDS)

    return {
        "last_backup_time": format_backup_time(ctx, last_backup_ts),
        "next_backup_time": format_backup_time(ctx, next_backup_at),
    }


def get_backup_status(ctx: Any) -> tuple[str, str]:
    """Return backup runtime status text and CSS class."""
    if is_backup_running(ctx):
        return "Running", "stat-green"
    try:
        rows = state_store_service.list_operations_by_status(
            Path(ctx.APP_STATE_DB_PATH),
            statuses=("intent", "in_progress"),
            limit=40,
        )
    except Exception:
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("op_type", "") or "").strip().lower() != "backup":
            continue
        return "Queued", "stat-yellow"
    return "Idle", "stat-yellow"
