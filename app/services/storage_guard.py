"""Shared storage guard for job start enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.filesystem_utils import format_file_size
from app.ports import ports
from app.services.dashboard_file_runtime import _snapshot_dir_size_cached


@dataclass(frozen=True)
class StorageSnapshot:
    total_bytes: int
    free_bytes: int
    free_percent: float | None
    used_percent: float | None


class StorageGuard:
    """Centralized storage checks for backup/restore/start flows."""

    def _disk_usage(self, ctx: Any) -> StorageSnapshot:
        try:
            total, _used, free = ports.filesystem.disk_usage(ctx.BACKUP_DIR)
        except OSError:
            return StorageSnapshot(0, 0, None, None)
        total = int(total)
        free = int(free)
        used = max(0, total - free)
        if total <= 0:
            return StorageSnapshot(total, free, None, None)
        free_percent = (100.0 * free / total)
        used_percent = (100.0 * used / total)
        return StorageSnapshot(total, free, free_percent, used_percent)

    def _threshold_percent(self, ctx: Any) -> float:
        try:
            return float(getattr(ctx, "LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT", 10.0) or 10.0)
        except (TypeError, ValueError):
            return 10.0

    def estimate_job_bytes(self, ctx: Any, job_type: object, filename: object = None) -> int:
        job = str(job_type or "").strip().lower()
        if job in {"backup", "backup_manual", "backup_auto", "backup_session", "backup_periodic"}:
            world_dir = getattr(ctx, "WORLD_DIR", None)
            if world_dir is None:
                return 0
            try:
                return int(_snapshot_dir_size_cached(Path(world_dir)))
            except Exception:
                return 0
        if job in {"restore", "restore_backup"}:
            name = str(filename or "").strip()
            if not name:
                return 0
            try:
                path = Path(ctx.BACKUP_DIR) / name
                if not path.exists():
                    return 0
                return int(path.stat().st_size)
            except Exception:
                return 0
        return 0

    def is_below_minimum(self, ctx: Any) -> bool:
        snapshot = self._disk_usage(ctx)
        threshold = self._threshold_percent(ctx)
        if snapshot.free_percent is None:
            return False
        return snapshot.free_percent < threshold

    def is_storage_sufficient(self, ctx: Any, job_type: object, filename: object = None) -> bool:
        snapshot = self._disk_usage(ctx)
        threshold = self._threshold_percent(ctx)
        if snapshot.free_percent is not None and snapshot.free_percent < threshold:
            return False
        estimate = self.estimate_job_bytes(ctx, job_type, filename=filename)
        if estimate > 0 and snapshot.free_bytes > 0 and snapshot.free_bytes < estimate:
            return False
        return True

    def needs_emergency_shutdown(self, ctx: Any) -> bool:
        snapshot = self._disk_usage(ctx)
        threshold = self._threshold_percent(ctx)
        if snapshot.free_percent is not None and snapshot.free_percent < threshold:
            return True
        estimate = self.estimate_job_bytes(ctx, "backup")
        return estimate > 0 and snapshot.free_bytes > 0 and snapshot.free_bytes < estimate

    def block_message(self, ctx: Any, job_type: object, filename: object = None) -> str:
        snapshot = self._disk_usage(ctx)
        threshold = self._threshold_percent(ctx)
        usage_text = ""
        getter = getattr(ctx, "get_storage_usage", None)
        if callable(getter):
            try:
                usage_text = str(getter() or "")
            except Exception:
                usage_text = ""
        available_text = "unknown"
        if snapshot.free_percent is not None:
            available_text = f"{snapshot.free_percent:.1f}%"
        estimate = self.estimate_job_bytes(ctx, job_type, filename=filename)
        estimate_text = format_file_size(estimate) if estimate > 0 else ""
        if snapshot.free_percent is not None and snapshot.free_percent < threshold:
            suffix = f" ({usage_text})" if usage_text else ""
            return (
                f"Low storage space: only {available_text} free{suffix}. "
                f"Operation is blocked below {threshold:.0f}% free."
            )
        if estimate > 0 and snapshot.free_bytes > 0 and snapshot.free_bytes < estimate:
            free_text = format_file_size(snapshot.free_bytes)
            job = str(job_type or "job")
            return (
                f"Insufficient free space for {job}: {free_text} free, "
                f"need about {estimate_text} to proceed."
            )
        return "Low storage space: operation blocked by safety guard."

    def emergency_message(self, ctx: Any) -> str:
        snapshot = self._disk_usage(ctx)
        threshold = self._threshold_percent(ctx)
        if snapshot.free_percent is not None and snapshot.free_percent < threshold:
            return self.block_message(ctx, "start")
        estimate = self.estimate_job_bytes(ctx, "backup")
        if estimate > 0 and snapshot.free_bytes > 0 and snapshot.free_bytes < estimate:
            free_text = format_file_size(snapshot.free_bytes)
            need_text = format_file_size(estimate)
            return f"Storage too low for backup: {free_text} free, need {need_text}."
        return "Storage is critically low."

