"""Backup scheduler missed-run tracking helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from app.services.maintenance_context import as_ctx


class MissedBackupEvent(TypedDict, total=False):
    at: str
    reason: str
    count: int
    due_runs: int
    interval_seconds: int


class BackupNonNormalData(TypedDict):
    missed_runs: list[MissedBackupEvent]


def _coerce_event(raw: object) -> MissedBackupEvent | None:
    if not isinstance(raw, dict):
        return None
    event: MissedBackupEvent = {}
    at = raw.get("at")
    reason = raw.get("reason")
    count = raw.get("count")
    due_runs = raw.get("due_runs")
    interval_seconds = raw.get("interval_seconds")
    if at is not None:
        event["at"] = str(at)
    if reason is not None:
        event["reason"] = str(reason)
    if count is not None:
        event["count"] = _to_int(count, 1)
    if due_runs is not None:
        event["due_runs"] = _to_int(due_runs)
    if interval_seconds is not None:
        event["interval_seconds"] = _to_int(interval_seconds)
    return event


def _to_int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            return int(value)
    except Exception:
        pass
    return default


def _backup_data_dir(ctx: Any) -> Path:
    ctx = as_ctx(ctx)
    return Path(ctx.session_state.session_file).parent


def _backup_non_normal_path(ctx: Any) -> Path:
    return _backup_data_dir(ctx) / "backup_non_normal.txt"


def _backup_now_iso(ctx: Any) -> str:
    ctx = as_ctx(ctx)
    tz = getattr(ctx, "DISPLAY_TZ", None)
    now = datetime.now(tz) if tz else datetime.utcnow()
    return now.isoformat(timespec="seconds")


def _backup_default_non_normal() -> BackupNonNormalData:
    return {"missed_runs": []}


def _backup_atomic_write_json(path: str | Path, payload: BackupNonNormalData) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _backup_load_json(path: str | Path, default: BackupNonNormalData) -> dict[str, object] | BackupNonNormalData:
    path = Path(path)
    if not path.exists():
        return default
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return loaded if isinstance(loaded, dict) else default


def load_backup_non_normal(ctx: Any) -> BackupNonNormalData:
    default = _backup_default_non_normal()
    loaded = _backup_load_json(_backup_non_normal_path(ctx), default)
    data: BackupNonNormalData = {"missed_runs": []}
    missed_runs = loaded.get("missed_runs")
    if isinstance(missed_runs, list):
        data["missed_runs"] = [event for entry in missed_runs if (event := _coerce_event(entry)) is not None]
    return data


def record_missed_backup(
    ctx: Any,
    *,
    count: object,
    reason: object = "interval_gap",
    due_runs: object | None = None,
    interval_seconds: object | None = None,
) -> BackupNonNormalData:
    missed = _to_int(count)
    if missed <= 0:
        return load_backup_non_normal(ctx)
    data = load_backup_non_normal(ctx)
    event: MissedBackupEvent = {
        "at": _backup_now_iso(ctx),
        "reason": str(reason or "interval_gap"),
        "count": missed,
    }
    if due_runs is not None:
        event["due_runs"] = _to_int(due_runs)
    if interval_seconds is not None:
        event["interval_seconds"] = _to_int(interval_seconds)
    data["missed_runs"].append(event)
    data["missed_runs"] = data["missed_runs"][-100:]
    _backup_atomic_write_json(_backup_non_normal_path(ctx), data)
    return data


def get_missed_backup_count(ctx: Any) -> int:
    data = load_backup_non_normal(ctx)
    missed = data.get("missed_runs") if isinstance(data, dict) else None
    if not isinstance(missed, list):
        return 0
    total = 0
    for entry in missed:
        if isinstance(entry, dict) and "count" in entry:
            try:
                total += _to_int(entry.get("count", 1) or 1, 1)
                continue
            except Exception:
                pass
        total += 1
    return total


__all__ = [
    "load_backup_non_normal",
    "record_missed_backup",
    "get_missed_backup_count",
]
