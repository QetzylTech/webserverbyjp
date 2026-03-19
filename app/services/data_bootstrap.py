"""Boot-time data directory validation and preinitialization."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.core import state_store as state_store_service


_REQUIRED_DB_TABLES = {
    "users",
    "device_fallmap",
    "cleanup_store",
    "cleanup_history",
    "restore_name_runs",
    "restore_runs",
    "file_records",
    "file_record_history",
}


def _utc_stamp():
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _archive_incompatible(path, *, data_dir, old_dir):
    rel = path.relative_to(data_dir)
    destination = old_dir / rel.parent / f"{rel.name}.{_utc_stamp()}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    path.replace(destination)
    return destination


def _ensure_parent(path):
    path.parent.mkdir(parents=True, exist_ok=True)


def _ensure_text_file(path, default_text, validator, *, data_dir, old_dir, log):
    _ensure_parent(path)
    if not path.exists():
        path.write_text(default_text, encoding="utf-8")
        return
    if path.is_dir():
        archived = _archive_incompatible(path, data_dir=data_dir, old_dir=old_dir)
        log("data-bootstrap-archive", f"{path} -> {archived} (expected file)")
        path.write_text(default_text, encoding="utf-8")
        return
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    if validator(text):
        return
    archived = _archive_incompatible(path, data_dir=data_dir, old_dir=old_dir)
    log("data-bootstrap-archive", f"{path} -> {archived} (incompatible content)")
    path.write_text(default_text, encoding="utf-8")


def _is_session_text_valid(text):
    raw = str(text or "").strip()
    if not raw:
        return True
    try:
        return float(raw) > 0
    except ValueError:
        return False


def _is_backup_state_text_valid(text):
    return str(text or "").strip().lower() in {"", "true", "false"}


def _is_cleanup_non_normal_valid(text):
    try:
        payload = json.loads(text or "{}")
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    if not isinstance(payload.get("missed_runs", []), list):
        return False
    if not isinstance(payload.get("last_ack_at", ""), str):
        return False
    if not isinstance(payload.get("last_ack_by", ""), str):
        return False
    return True


def _is_json_dict_valid(text):
    raw = str(text or "").strip()
    if not raw:
        return True
    try:
        payload = json.loads(raw)
    except Exception:
        return False
    return isinstance(payload, dict)


def _is_db_compatible(db_path):
    if not db_path.exists():
        return False
    if db_path.is_dir():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
    except Exception:
        return False
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    except Exception:
        conn.close()
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass
    tables = {str(row[0]) for row in rows}
    return _REQUIRED_DB_TABLES.issubset(tables)


def ensure_data_bootstrap(*, data_dir, app_state_db_path, log_mcweb_log, log_mcweb_exception):
    """Ensure required data files exist and are compatible with current app schema."""
    data_dir = Path(data_dir)
    db_path = Path(app_state_db_path)
    old_dir = data_dir / "old_app_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    old_dir.mkdir(parents=True, exist_ok=True)

    def _log(event, message):
        try:
            log_mcweb_log(event, command="data-bootstrap", rejection_message=str(message)[:700])
        except Exception:
            pass

    _ensure_text_file(
        data_dir / "session.txt",
        "",
        _is_session_text_valid,
        data_dir=data_dir,
        old_dir=old_dir,
        log=_log,
    )
    _ensure_text_file(
        data_dir / "state.txt",
        "false\n",
        _is_backup_state_text_valid,
        data_dir=data_dir,
        old_dir=old_dir,
        log=_log,
    )
    _ensure_text_file(
        data_dir / "cleanup_non_normal.txt",
        json.dumps({"missed_runs": [], "last_ack_at": "", "last_ack_by": ""}, indent=2) + "\n",
        _is_cleanup_non_normal_valid,
        data_dir=data_dir,
        old_dir=old_dir,
        log=_log,
    )
    _ensure_text_file(
        data_dir / "restore.history",
        "",
        lambda _text: True,
        data_dir=data_dir,
        old_dir=old_dir,
        log=_log,
    )
    if db_path.exists() and not _is_db_compatible(db_path):
        archived = _archive_incompatible(db_path, data_dir=data_dir, old_dir=old_dir)
        _log("data-bootstrap-archive", f"{db_path} -> {archived} (incompatible sqlite schema)")

    try:
        state_store_service.initialize_state_db(
            db_path=db_path,
            log_exception=log_mcweb_exception,
        )
    except Exception as exc:
        if callable(log_mcweb_exception):
            log_mcweb_exception("data-bootstrap/initialize-state-db", exc)
        return

    # On app reboot, clear any restore operations that were left in intent/in_progress.
    try:
        active_ops = state_store_service.list_operations_by_status(
            db_path,
            statuses=("intent", "in_progress"),
            limit=200,
        )
        restore_updates = []
        for op in active_ops:
            if not isinstance(op, dict):
                continue
            if str(op.get("op_type", "") or "").strip().lower() != "restore":
                continue
            op_id = str(op.get("op_id", "") or "").strip()
            if not op_id:
                continue
            restore_updates.append(
                {
                    "op_id": op_id,
                    "status": "failed",
                    "error_code": "app_reboot_reset",
                    "checkpoint": "app_reboot_reset",
                    "message": "Restore operation cleared on app reboot.",
                    "finished": True,
                }
            )
        if restore_updates:
            state_store_service.update_operations_batch(db_path, updates=restore_updates)
            _log("restore-reboot-clear", f"Cleared {len(restore_updates)} restore operations on reboot.")
    except Exception as exc:
        if callable(log_mcweb_exception):
            log_mcweb_exception("data-bootstrap/restore-reboot-clear", exc)
