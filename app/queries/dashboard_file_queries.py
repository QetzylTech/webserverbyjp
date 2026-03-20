"""Read-side helpers for dashboard file and log routes."""

from __future__ import annotations

from collections import deque
from datetime import tzinfo
import gzip
from pathlib import Path
from typing import Any


def read_view_file_content(file_path: Path, safe_name: str, *, max_bytes: int = 2_000_000) -> tuple[str | None, str | None]:
    """Read text content from a log/file path with size and gzip handling."""
    try:
        if safe_name.lower().endswith(".gz"):
            tail_chunks: deque[str] = deque()
            tail_len = 0
            truncated = False
            with gzip.open(file_path, "rt", encoding="utf-8", errors="ignore") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    tail_chunks.append(chunk)
                    tail_len += len(chunk)
                    while tail_len > max_bytes and tail_chunks:
                        truncated = True
                        overflow = tail_len - max_bytes
                        head = tail_chunks[0]
                        if len(head) <= overflow:
                            tail_len -= len(head)
                            tail_chunks.popleft()
                        else:
                            tail_chunks[0] = head[overflow:]
                            tail_len -= overflow
            text = "".join(tail_chunks)
            if truncated:
                text = f"[truncated to last {max_bytes} characters]\n{text}"
            return text, None

        size = file_path.stat().st_size
        if size > max_bytes:
            with file_path.open("rb") as f:
                f.seek(max(0, size - max_bytes))
                raw = f.read(max_bytes)
            return "[truncated to last 2000000 bytes]\n" + raw.decode("utf-8", errors="ignore"), None
        return file_path.read_text(encoding="utf-8", errors="ignore"), None
    except OSError:
        return None, "Unable to read file."


def snapshot_root_dir(state: Any) -> Path:
    """Return the snapshot root directory for the current runtime."""
    return Path(getattr(state, "AUTO_SNAPSHOT_DIR", "") or (state["BACKUP_DIR"] / "snapshots"))


def resolve_snapshot_dir(state: Any, snapshot_name: str) -> tuple[Path | None, str]:
    """Validate and resolve a snapshot directory request."""
    if not snapshot_name:
        return None, ""
    safe_name = Path(snapshot_name).name
    if safe_name != snapshot_name:
        return None, ""
    base_dir = snapshot_root_dir(state)
    candidate = base_dir / safe_name
    try:
        base_resolved = base_dir.resolve()
        candidate_resolved = candidate.resolve()
        candidate_resolved.relative_to(base_resolved)
    except (OSError, ValueError):
        return None, ""
    if not candidate_resolved.exists() or not candidate_resolved.is_dir():
        return None, ""
    return candidate_resolved, safe_name


def log_file_source_spec(state: Any, source: str) -> dict[str, Any] | None:
    """Return the configured log-file source spec for a requested key."""
    normalized = str(source or "").strip().lower()
    log_dir = state["MCWEB_LOG_FILE"].parent
    if normalized == "minecraft":
        return {
            "key": "minecraft",
            "base_dir": state["MINECRAFT_LOGS_DIR"],
            "patterns": ("*.log", "*.gz"),
            "download_base": "/download/minecraft-logs",
            "view_base": "/view-file/minecraft_logs",
        }
    if normalized == "crash":
        return {
            "key": "crash",
            "base_dir": state["CRASH_REPORTS_DIR"],
            "patterns": ("*.txt",),
            "download_base": "/download/log-files/crash",
            "view_base": "/view-log-file/crash",
        }
    if normalized == "backup":
        return {
            "key": "backup",
            "base_dir": log_dir,
            "patterns": ("backup.log*",),
            "download_base": "/download/log-files/backup",
            "view_base": "/view-log-file/backup",
        }
    if normalized == "mcweb":
        return {
            "key": "mcweb",
            "base_dir": log_dir,
            "patterns": ("mcweb_actions.log*",),
            "download_base": "/download/log-files/mcweb",
            "view_base": "/view-log-file/mcweb",
        }
    if normalized == "mcweb_log":
        return {
            "key": "mcweb_log",
            "base_dir": log_dir,
            "patterns": ("mcweb.log*",),
            "download_base": "/download/log-files/mcweb_log",
            "view_base": "/view-log-file/mcweb_log",
        }
    if normalized == "restore":
        return {
            "key": "restore",
            "base_dir": log_dir,
            "patterns": ("restore_*.log", "restore.log*"),
            "download_base": "/download/log-files/restore",
            "view_base": "/view-log-file/restore",
        }
    return None


def log_file_items_from_spec(state: Any, spec: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return merged file items for a log source spec."""
    if not spec:
        return []
    merged_by_name: dict[str, dict[str, Any]] = {}
    for pattern in spec["patterns"]:
        for item in state["_list_download_files"](spec["base_dir"], pattern, state["DISPLAY_TZ"]):
            merged_by_name[item["name"]] = dict(item)
    items = list(merged_by_name.values())
    items.sort(key=lambda item: item.get("mtime", 0), reverse=True)
    return items


def resolve_log_file(state: Any, source: str, filename: str) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a log-file request and return the spec + safe filename."""
    spec = log_file_source_spec(state, source)
    if spec is None:
        return None, None
    safe_name = state["_safe_filename_in_dir"](spec["base_dir"], filename)
    if safe_name is None:
        return spec, None
    return spec, safe_name
