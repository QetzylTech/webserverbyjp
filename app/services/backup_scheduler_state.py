"""Backup scheduler missed-run tracking helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.services.maintenance_context import as_ctx


def _backup_data_dir(ctx):
    ctx = as_ctx(ctx)
    return Path(ctx.session_state.session_file).parent


def _backup_non_normal_path(ctx):
    return _backup_data_dir(ctx) / "backup_non_normal.txt"


def _backup_now_iso(ctx):
    ctx = as_ctx(ctx)
    tz = getattr(ctx, "DISPLAY_TZ", None)
    now = datetime.now(tz) if tz else datetime.utcnow()
    return now.isoformat(timespec="seconds")


def _backup_default_non_normal():
    return {"missed_runs": []}


def _backup_atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def _backup_load_json(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return loaded if isinstance(loaded, dict) else default


def load_backup_non_normal(ctx):
    default = _backup_default_non_normal()
    loaded = _backup_load_json(_backup_non_normal_path(ctx), default)
    data = {"missed_runs": []}
    if isinstance(loaded.get("missed_runs"), list):
        data["missed_runs"] = loaded["missed_runs"]
    return data


def record_missed_backup(ctx, *, count, reason="interval_gap", due_runs=None, interval_seconds=None):
    missed = int(count or 0)
    if missed <= 0:
        return load_backup_non_normal(ctx)
    data = load_backup_non_normal(ctx)
    event = {
        "at": _backup_now_iso(ctx),
        "reason": str(reason or "interval_gap"),
        "count": missed,
    }
    if due_runs is not None:
        event["due_runs"] = int(due_runs)
    if interval_seconds is not None:
        event["interval_seconds"] = int(interval_seconds)
    data["missed_runs"].append(event)
    data["missed_runs"] = data["missed_runs"][-100:]
    _backup_atomic_write_json(_backup_non_normal_path(ctx), data)
    return data


def get_missed_backup_count(ctx):
    data = load_backup_non_normal(ctx)
    missed = data.get("missed_runs") if isinstance(data, dict) else None
    if not isinstance(missed, list):
        return 0
    total = 0
    for entry in missed:
        if isinstance(entry, dict) and "count" in entry:
            try:
                total += int(entry.get("count", 1) or 1)
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
