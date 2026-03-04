"""Unified in-memory inventory index for backups, snapshots, and old worlds."""

from __future__ import annotations

import threading
from pathlib import Path


_INDEX_LOCK = threading.Lock()
_INDEX_CACHE = {
    "backup_root": "",
    "snapshot_root": "",
    "old_worlds_root": "",
    "backup_mtime_ns": -1,
    "snapshot_mtime_ns": -1,
    "old_worlds_mtime_ns": -1,
    "backup_zip_paths": [],
    "snapshot_dir_paths": [],
    "old_world_top_entries": [],
    "old_world_nested_zip_paths": [],
}


def _safe_dir_mtime_ns(path):
    try:
        return int(Path(path).stat().st_mtime_ns)
    except OSError:
        return -1


def _refresh_index(backup_root, snapshot_root, old_worlds_root):
    backup_paths = []
    if backup_root.exists() and backup_root.is_dir():
        for entry in backup_root.glob("*.zip"):
            if entry.is_file():
                backup_paths.append(str(entry))

    snapshot_paths = []
    if snapshot_root.exists() and snapshot_root.is_dir():
        for entry in snapshot_root.iterdir():
            if entry.is_dir():
                snapshot_paths.append(str(entry))

    old_world_entries = []
    old_world_nested_zips = []
    if old_worlds_root.exists() and old_worlds_root.is_dir():
        for entry in old_worlds_root.iterdir():
            if entry.is_dir() or entry.is_file():
                old_world_entries.append(str(entry))
        for entry in old_worlds_root.rglob("*.zip"):
            if not entry.is_file():
                continue
            if entry.parent == old_worlds_root:
                continue
            old_world_nested_zips.append(str(entry))

    _INDEX_CACHE.update(
        {
            "backup_root": str(backup_root),
            "snapshot_root": str(snapshot_root),
            "old_worlds_root": str(old_worlds_root),
            "backup_mtime_ns": _safe_dir_mtime_ns(backup_root),
            "snapshot_mtime_ns": _safe_dir_mtime_ns(snapshot_root),
            "old_worlds_mtime_ns": _safe_dir_mtime_ns(old_worlds_root),
            "backup_zip_paths": backup_paths,
            "snapshot_dir_paths": snapshot_paths,
            "old_world_top_entries": old_world_entries,
            "old_world_nested_zip_paths": old_world_nested_zips,
        }
    )


def get_inventory(backup_root, snapshot_root, old_worlds_root):
    """Return indexed inventory paths for backup/snapshot/old-world artifacts."""
    backup_path = Path(backup_root)
    snapshot_path = Path(snapshot_root)
    old_worlds_path = Path(old_worlds_root)
    backup_mtime_ns = _safe_dir_mtime_ns(backup_path)
    snapshot_mtime_ns = _safe_dir_mtime_ns(snapshot_path)
    old_worlds_mtime_ns = _safe_dir_mtime_ns(old_worlds_path)
    with _INDEX_LOCK:
        fresh = (
            _INDEX_CACHE["backup_root"] == str(backup_path)
            and _INDEX_CACHE["snapshot_root"] == str(snapshot_path)
            and _INDEX_CACHE["old_worlds_root"] == str(old_worlds_path)
            and int(_INDEX_CACHE["backup_mtime_ns"]) == backup_mtime_ns
            and int(_INDEX_CACHE["snapshot_mtime_ns"]) == snapshot_mtime_ns
            and int(_INDEX_CACHE["old_worlds_mtime_ns"]) == old_worlds_mtime_ns
        )
        if not fresh:
            _refresh_index(backup_path, snapshot_path, old_worlds_path)
        return {
            "backup_zip_paths": [Path(p) for p in _INDEX_CACHE["backup_zip_paths"]],
            "snapshot_dir_paths": [Path(p) for p in _INDEX_CACHE["snapshot_dir_paths"]],
            "old_world_top_entries": [Path(p) for p in _INDEX_CACHE["old_world_top_entries"]],
            "old_world_nested_zip_paths": [Path(p) for p in _INDEX_CACHE["old_world_nested_zip_paths"]],
        }
