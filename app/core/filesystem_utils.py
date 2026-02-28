"""Filesystem helpers for file listings, safe paths, and lightweight reads."""

from datetime import datetime
from pathlib import Path


def format_file_size(num_bytes):
    """Format bytes into a human-readable string (B/KB/MB/GB/TB)."""
    value = float(max(0, num_bytes or 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.1f} {units[idx]}"


def list_download_files(base_dir, pattern, display_tz):
    """Return file metadata sorted newest-first for download listings."""
    items = []
    if not base_dir.exists() or not base_dir.is_dir():
        return items

    for path in base_dir.glob(pattern):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        ts = stat.st_mtime
        items.append({
            "name": path.name,
            "mtime": ts,
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(ts, tz=display_tz).strftime("%b %d, %Y %I:%M:%S %p %Z"),
            "size_text": format_file_size(stat.st_size),
        })

    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items


def read_recent_file_lines(path, limit):
    """Read and return the last ``limit`` lines from a text file."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    lines = text.splitlines()
    if len(lines) > limit:
        lines = lines[-limit:]
    return lines


def safe_file_mtime_ns(path):
    """Return file ``mtime_ns`` or ``None`` when unavailable."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def safe_filename_in_dir(base_dir, filename):
    """Validate and return a direct-child filename within ``base_dir``."""
    if not filename:
        return None
    name = Path(filename).name
    if name != filename:
        return None
    candidate = base_dir / name
    try:
        base_resolved = base_dir.resolve()
        candidate_resolved = candidate.resolve()
    except OSError:
        return None
    try:
        candidate_resolved.relative_to(base_resolved)
    except ValueError:
        return None
    if not candidate_resolved.exists() or not candidate_resolved.is_file():
        return None
    return name
