"""Restore status tracking helpers."""

import threading
import time


def _restore_status_defaults():
    return {
        "job_id": "",
        "running": False,
        "seq": 0,
        "events": [],
        "result": None,
    }


def _ensure_restore_status_state(ctx):
    state = getattr(ctx, "restore_status", None)
    if not isinstance(state, dict):
        state = _restore_status_defaults()
        try:
            setattr(ctx, "restore_status", state)
        except Exception:
            pass
    else:
        for key, value in _restore_status_defaults().items():
            state.setdefault(key, value if not isinstance(value, list) else list(value))

    lock = getattr(ctx, "restore_status_lock", None)
    if not isinstance(lock, threading.Lock):
        lock = threading.Lock()
        try:
            setattr(ctx, "restore_status_lock", lock)
        except Exception:
            pass
    return state, lock


def append_restore_event(ctx, message):
    state, lock = _ensure_restore_status_state(ctx)
    text = str(message or "").strip()
    if not text:
        return None
    with lock:
        state["seq"] = int(state.get("seq", 0)) + 1
        event = {
            "seq": state["seq"],
            "message": text,
            "at": time.time(),
        }
        events = state.setdefault("events", [])
        events.append(event)
        if len(events) > 120:
            del events[:-120]
    return event


def get_restore_status(ctx, since_seq=0, job_id=None):
    state, lock = _ensure_restore_status_state(ctx)
    try:
        since = int(since_seq or 0)
    except (TypeError, ValueError):
        since = 0
    requested_job_id = str(job_id or "").strip()
    with lock:
        current_job_id = str(state.get("job_id", "") or "")
        if requested_job_id and current_job_id and requested_job_id != current_job_id:
            return {
                "ok": True,
                "job_id": requested_job_id,
                "running": False,
                "seq": int(state.get("seq", 0) or 0),
                "events": [],
                "result": None,
            }
        events = [dict(item) for item in state.get("events", []) if int(item.get("seq", 0) or 0) > since]
        result = state.get("result")
        return {
            "ok": True,
            "job_id": current_job_id,
            "running": bool(state.get("running")),
            "seq": int(state.get("seq", 0) or 0),
            "events": events,
            "result": dict(result) if isinstance(result, dict) else result,
        }
