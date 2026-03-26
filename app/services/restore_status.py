"""Restore status tracking helpers."""

from datetime import datetime
from pathlib import Path
import time
import threading
from typing import Any

_LOCK_TYPE = type(threading.Lock())

from app.core import state_store as state_store_service
from app.services import log_stream_service as log_stream_service
from app.services.operation_state import has_pending_operation


def _coerce_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip() or str(default))
        except ValueError:
            return default
    return default


def _restore_status_defaults() -> dict[str, object]:
    return {
        "job_id": "",
        "running": False,
        "seq": 0,
        "events": [],
        "result": None,
        "log_file": None,
    }


def _ensure_restore_status_state(ctx: Any) -> tuple[dict[str, object], threading.Lock]:
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


def append_restore_event(ctx: Any, message: object) -> dict[str, object] | None:
    state, lock = _ensure_restore_status_state(ctx)
    text = str(message or "").strip()
    if not text:
        return None
    with lock:
        state["seq"] = _coerce_int(state.get("seq", 0)) + 1
        event = {
            "seq": state["seq"],
            "message": text,
            "at": time.time(),
        }
        events_obj = state.setdefault("events", [])
        if not isinstance(events_obj, list):
            events_obj = []
            state["events"] = events_obj
        events: list[dict[str, object]] = events_obj
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

        def _append_log_line(path: object) -> None:
            if not path:
                return
            log_file = path if isinstance(path, Path) else Path(str(path))
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


def get_restore_status(ctx: Any, since_seq: object = 0, job_id: object = None) -> dict[str, object]:
    state, lock = _ensure_restore_status_state(ctx)
    since = _coerce_int(since_seq)
    requested_job_id = str(job_id or "").strip()
    db_path = getattr(ctx, "APP_STATE_DB_PATH", None)
    process_role = str(getattr(ctx, "PROCESS_ROLE", "all") or "all").strip().lower()
    with lock:
        if process_role == "web" and state.get("running") and not has_pending_operation(ctx, "restore"):
            state["running"] = False
            state["job_id"] = ""
        current_job_id = str(state.get("job_id", "") or "")
        raw_events = state.get("events", [])
        event_items = raw_events if isinstance(raw_events, list) else []
        events = [dict(item) for item in event_items if isinstance(item, dict) and _coerce_int(item.get("seq", 0)) > since]
        if requested_job_id and current_job_id and requested_job_id != current_job_id:
            if events:
                return {
                    "ok": True,
                    "job_id": requested_job_id,
                    "running": False,
                    "seq": _coerce_int(state.get("seq", 0)),
                    "events": [],
                    "result": None,
                }
            current_job_id = ""
        result = state.get("result")
        current_log_file = str(state.get("log_file", "") or "").strip()
        current_log_name = Path(current_log_file).name if current_log_file else ""
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
                            "seq": _coerce_int(row.get("id", 0) if isinstance(row, dict) else 0),
                            "message": str(payload.get("message", "") or ""),
                            "at": payload.get("at") or row.get("created_at"),
                        }
                    )
                if not current_job_id and events:
                    if requested_job_id:
                        current_job_id = requested_job_id
                    else:
                        last_row = rows[-1] if rows else {}
                        last_payload = last_row.get("payload", {}) if isinstance(last_row, dict) else {}
                        if not isinstance(last_payload, dict):
                            last_payload = {}
                        last_job_id = str(last_payload.get("job_id", "") or "")
                        current_job_id = last_job_id or current_job_id
        seq_value = _coerce_int(state.get("seq", 0))
        if events:
            try:
                seq_value = max(seq_value, _coerce_int(events[-1].get("seq", seq_value), seq_value))
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
            "log_file": current_log_name,
            "result": dict(result) if isinstance(result, dict) else result,
        }


def restore_running_from_getter(getter: Any) -> bool:
    if not callable(getter):
        return False
    try:
        payload = getter(since_seq=0, job_id=None)
    except Exception:
        return False
    return bool(payload.get("running")) if isinstance(payload, dict) else False

