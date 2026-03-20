"""Optional low-overhead profiling helpers for MC Web runtime paths."""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Sequence


def _env_flag(name: str, default: str = "0") -> bool:
    value = str(os.getenv(name, default) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


ENABLED = _env_flag("MCWEB_PROFILE", "0")
_MAX_SAMPLES = int(max(100, int(os.getenv("MCWEB_PROFILE_MAX_SAMPLES", "4000"))))
_PROFILE_OUT = str(os.getenv("MCWEB_PROFILE_OUT", "") or "").strip()

_lock = threading.Lock()
_durations: defaultdict[str, list[float]] = defaultdict(list)
_counts: defaultdict[str, int] = defaultdict(int)
_errors: defaultdict[str, int] = defaultdict(int)
_gauges: dict[str, object] = {}
_op_checkpoints: dict[str, tuple[str, float]] = {}


def _append_sample(name: str, seconds: float) -> None:
    values = _durations[name]
    values.append(float(max(0.0, seconds)))
    if len(values) > _MAX_SAMPLES:
        del values[0 : len(values) - _MAX_SAMPLES]
    _counts[name] += 1


def incr_error(name: str, delta: int = 1) -> None:
    if not ENABLED:
        return
    with _lock:
        _errors[name] += int(max(1, delta))


def set_gauge(name: str, value: object) -> None:
    if not ENABLED:
        return
    with _lock:
        _gauges[name] = value


def record_duration(name: str, seconds: float) -> None:
    if not ENABLED:
        return
    with _lock:
        _append_sample(name, seconds)


@contextmanager
def timed(name: str) -> Iterator[None]:
    if not ENABLED:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    except Exception:
        incr_error(f"{name}.error")
        raise
    finally:
        record_duration(name, time.perf_counter() - started)


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * float(pct)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def mark_operation_checkpoint(op_id: str, checkpoint: str) -> None:
    if not ENABLED:
        return
    key = str(op_id or "").strip()
    cp = str(checkpoint or "").strip()
    if not key or not cp:
        return
    now = time.time()
    with _lock:
        prev = _op_checkpoints.get(key)
        _op_checkpoints[key] = (cp, now)
        if prev is None:
            return
        prev_cp, prev_ts = prev
        duration = max(0.0, now - float(prev_ts))
        _append_sample(f"operation.checkpoint.{prev_cp}->{cp}", duration)


def record_operation_transition(op_type: str, item: dict[str, object]) -> None:
    if not ENABLED or not isinstance(item, dict):
        return
    op_kind = str(op_type or item.get("op_type", "") or "unknown").strip().lower()

    def _iso_to_epoch(raw: object) -> float | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    intent_at = _iso_to_epoch(item.get("intent_at"))
    started_at = _iso_to_epoch(item.get("started_at"))
    finished_at = _iso_to_epoch(item.get("finished_at"))
    status = str(item.get("status", "") or "").strip().lower()

    with _lock:
        if intent_at and started_at:
            _append_sample(f"operation.{op_kind}.intent_to_in_progress", max(0.0, started_at - intent_at))
        if started_at and finished_at:
            _append_sample(f"operation.{op_kind}.in_progress_to_terminal", max(0.0, finished_at - started_at))
        if intent_at and finished_at and status in {"observed", "failed"}:
            _append_sample(f"operation.{op_kind}.intent_to_terminal", max(0.0, finished_at - intent_at))


def summary() -> dict[str, object]:
    with _lock:
        duration_copy: dict[str, list[float]] = {key: list(values) for key, values in _durations.items()}
        counts: dict[str, int] = dict(_counts)
        errors: dict[str, int] = dict(_errors)
        gauges: dict[str, object] = dict(_gauges)

    metrics: dict[str, dict[str, float | int]] = {}
    for key, values in duration_copy.items():
        ordered = sorted(values)
        total = float(sum(ordered))
        count = int(len(ordered))
        metrics[key] = {
            "count": count,
            "total_ms": total * 1000.0,
            "avg_ms": (total / count) * 1000.0 if count else 0.0,
            "p95_ms": _percentile(ordered, 0.95) * 1000.0 if count else 0.0,
            "p99_ms": _percentile(ordered, 0.99) * 1000.0 if count else 0.0,
            "max_ms": (ordered[-1] * 1000.0) if count else 0.0,
            "calls_total": int(counts.get(key, count)),
        }
    return {
        "enabled": ENABLED,
        "metrics": metrics,
        "errors": errors,
        "gauges": gauges,
        "captured_at_epoch": time.time(),
    }


def write_summary(path: str | Path) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = summary()
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return str(target)


def _auto_write() -> None:
    if not ENABLED or not _PROFILE_OUT:
        return
    try:
        write_summary(_PROFILE_OUT)
    except Exception:
        pass


atexit.register(_auto_write)
