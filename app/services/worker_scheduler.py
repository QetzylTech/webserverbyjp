"""Centralized worker scheduler for background thread lifecycles."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable


IntervalSource = Callable[[Any], float] | float | None


@dataclass(frozen=True)
class WorkerSpec:
    """Standard worker registration contract."""

    name: str
    target: Callable[..., Any]
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    interval_source: IntervalSource = None
    stop_signal_name: str = ""
    exception_policy: str = "log_and_continue"
    health_marker: str = ""


_REGISTRY_LOCK = threading.Lock()
_WORKERS: dict[str, dict[str, Any]] = {}


def _health_name(spec: WorkerSpec) -> str:
    return spec.health_marker or spec.name


def _set_health(ctx: Any, spec: WorkerSpec, **updates: Any) -> None:
    marker = _health_name(spec)
    record = {
        "running": False,
        "last_started_at": 0.0,
        "last_heartbeat_at": 0.0,
        "last_error": "",
        "exception_policy": spec.exception_policy,
        "interval_source": str(spec.interval_source),
        "stop_signal_name": spec.stop_signal_name,
    }
    record.update(updates)
    with _REGISTRY_LOCK:
        existing = _WORKERS.get(marker, {})
        if isinstance(existing, dict):
            existing.update(record)
            _WORKERS[marker] = existing
        else:
            _WORKERS[marker] = record
        store = getattr(ctx, "worker_health", None)
        if isinstance(store, dict):
            store[marker] = dict(_WORKERS[marker])


def get_worker_health_snapshot() -> dict[str, dict[str, Any]]:
    with _REGISTRY_LOCK:
        return {name: dict(payload) for name, payload in _WORKERS.items() if isinstance(payload, dict)}


def _resolve_stop_event(ctx: Any, spec: WorkerSpec) -> threading.Event | None:
    name = str(spec.stop_signal_name or "").strip()
    if not name:
        return None
    existing = getattr(ctx, name, None)
    if isinstance(existing, threading.Event):
        return existing
    event = threading.Event()
    try:
        setattr(ctx, name, event)
    except Exception:
        pass
    return event


def start_worker(ctx: Any, spec: WorkerSpec, *, daemon: bool = True, threading_module=threading):
    """Start one worker thread via the central scheduler."""
    marker = _health_name(spec)
    with _REGISTRY_LOCK:
        existing = _WORKERS.get(marker, {})
        thread = existing.get("thread") if isinstance(existing, dict) else None
        if thread is not None and getattr(thread, "is_alive", lambda: False)():
            return thread

    stop_event = _resolve_stop_event(ctx, spec)

    def _runner():
        _set_health(
            ctx,
            spec,
            running=True,
            last_started_at=time.time(),
            last_heartbeat_at=time.time(),
            last_error="",
            stop_signal_active=bool(stop_event is not None and stop_event.is_set()),
        )
        try:
            spec.target(*spec.args, **dict(spec.kwargs or {}))
        except Exception as exc:
            try:
                ctx.log_mcweb_exception(f"worker/{spec.name}", exc)
            except Exception:
                pass
            _set_health(ctx, spec, running=False, last_error=str(exc)[:700], last_heartbeat_at=time.time())
            if spec.exception_policy == "raise":
                raise
            return
        _set_health(ctx, spec, running=False, last_heartbeat_at=time.time())

    thread = threading_module.Thread(target=_runner, daemon=daemon)
    with _REGISTRY_LOCK:
        _WORKERS[marker] = {"thread": thread}
    thread.start()
    return thread


def start_detached(*, target: Callable[..., Any], args: tuple[Any, ...] = (), kwargs: dict[str, Any] | None = None, daemon: bool = True, threading_module=threading):
    """Start a one-off detached thread via the scheduler module."""
    thread = threading_module.Thread(target=target, args=args, kwargs=dict(kwargs or {}), daemon=daemon)
    thread.start()
    return thread
