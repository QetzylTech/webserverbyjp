"""Helpers for per-restore log filenames."""

from __future__ import annotations

import re
from datetime import datetime, tzinfo


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_backup_name(value: str) -> str:
    text = _SAFE_NAME_RE.sub("_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "restore"


def build_restore_log_filename(backup_filename: str, job_id: str, display_tz: tzinfo | None = None) -> str:
    safe = _sanitize_backup_name(backup_filename)
    now = datetime.now(tz=display_tz) if display_tz is not None else datetime.utcnow()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    job = re.sub(r"[^A-Za-z0-9]+", "", str(job_id or ""))[:12] or "job"
    return f"restore_{stamp}_{safe}_{job}.log"


def restore_log_safe_key(backup_filename: str) -> str:
    return _sanitize_backup_name(backup_filename)


def restore_log_safe_key_from_filename(filename: str) -> str | None:
    name = str(filename or "").strip()
    if not name.startswith("restore_") or not name.endswith(".log"):
        return None
    base = name[:-4]
    parts = base.split("_")
    if len(parts) < 4:
        return None
    # restore_<YYYYMMDD>_<HHMMSS>_<safe>_<jobid>
    safe_parts = parts[3:-1]
    if not safe_parts:
        return None
    return "_".join(safe_parts)
