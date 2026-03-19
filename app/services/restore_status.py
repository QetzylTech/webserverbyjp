"""Restore status tracking helpers."""

from datetime import datetime
from pathlib import Path
import time
import threading

_LOCK_TYPE = type(threading.Lock())

from app.core import state_store as state_store_service
from app.services import log_stream_service as log_stream_service
from app.services.operation_state import has_pending_operation

def _restore_status_defaults():
    return {
        "job_id": "",
        "running": False,
        "seq": 0,
        "events": [],
        "result": None,
        "log_file": None,
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
    if not isinstance(lock, _LOCK_TYPE):
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
        job_id = str(state.get("job_id", "") or "")
    try:
        db_path = getattr(ctx, "APP_STATE_DB_PATH", None)
        if db_path:
            state_store_service.append_event(
                db_path,
                topic="restore_log",
                payload={
                    "job_id": job_id,
                    "message": text,
                    "at": event.get("at"),
                },
            )
    except Exception:
        pass
    try:
        stamp = datetime.now(tz=getattr(ctx, "DISPLAY_TZ", None)).strftime("%Y-%m-%d %H:%M:%S %Z")
        line = f"{stamp} | {text}\n"

        def _append_log_line(path):
            if not path:
                return
            log_file = Path(path)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(line)

        log_path = state.get("log_file") or getattr(ctx, "RESTORE_LOG_FILE", None)
        _append_log_line(log_path)
        aggregate_path = getattr(ctx, "RESTORE_LOG_FILE", None)
        if aggregate_path and str(aggregate_path) != str(log_path):
            _append_log_line(aggregate_path)
    except Exception:
        pass
    try:
        log_stream_service.publish_log_stream_line(ctx, "restore", text)
    except Exception:
        pass
    return event


def get_restore_status(ctx, since_seq=0, job_id=None):
    state, lock = _ensure_restore_status_state(ctx)
    try:
        since = int(since_seq or 0)
    except (TypeError, ValueError):
        since = 0
    requested_job_id = str(job_id or "").strip()
    db_path = getattr(ctx, "APP_STATE_DB_PATH", None)
    process_role = str(getattr(ctx, "PROCESS_ROLE", "all") or "all").strip().lower()
    with lock:
        if process_role == "web" and state.get("running") and not has_pending_operation(ctx, "restore"):
            state["running"] = False
            state["job_id"] = ""
        current_job_id = str(state.get("job_id", "") or "")
        events = [dict(item) for item in state.get("events", []) if int(item.get("seq", 0) or 0) > since]
        if requested_job_id and current_job_id and requested_job_id != current_job_id:
            if events:
                return {
                    "ok": True,
                    "job_id": requested_job_id,
                    "running": False,
                    "seq": int(state.get("seq", 0) or 0),
                    "events": [],
                    "result": None,
                }
            current_job_id = ""
        result = state.get("result")
        if not events and db_path:
            try:
                rows = state_store_service.list_events_since(
                    db_path,
                    topic="restore_log",
                    since_id=since,
                    limit=400,
                )
            except Exception:
                rows = []
            if rows:
                events = []
                for row in rows:
                    payload = row.get("payload") if isinstance(row, dict) else {}
                    if not isinstance(payload, dict):
                        payload = {}
                    row_job_id = str(payload.get("job_id", "") or "")
                    if requested_job_id and row_job_id and requested_job_id != row_job_id:
                        continue
                    events.append(
                        {
                            "seq": int(row.get("id", 0) or 0),
                            "message": str(payload.get("message", "") or ""),
                            "at": payload.get("at") or row.get("created_at"),
                        }
                    )
                if not current_job_id and events:
                    if requested_job_id:
                        current_job_id = requested_job_id
                    else:
                        last_job_id = str((rows[-1].get("payload") or {}).get("job_id", "") or "")
                        current_job_id = last_job_id or current_job_id
        seq_value = int(state.get("seq", 0) or 0)
        if events:
            try:
                seq_value = max(seq_value, int(events[-1].get("seq", seq_value) or seq_value))
            except Exception:
                pass
        if process_role == "web" and not state.get("running") and has_pending_operation(ctx, "restore"):
            state["running"] = True
        return {
            "ok": True,
            "job_id": current_job_id,
            "running": bool(state.get("running")),
            "seq": seq_value,
            "events": events,
            "result": dict(result) if isinstance(result, dict) else result,
        }


def restore_running_from_getter(getter):
    if not callable(getter):
        return False
    try:
        payload = getter(since_seq=0, job_id=None)
    except Exception:
        return False
    return bool(payload.get("running")) if isinstance(payload, dict) else False

