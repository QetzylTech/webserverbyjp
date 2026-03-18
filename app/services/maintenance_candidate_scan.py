"""Maintenance candidate discovery and file/world inventory helpers."""

import os
import re
import threading
import time
from pathlib import Path

from app.ports import ports
from app.services.maintenance_state_store import _cleanup_data_dir
from app.services import file_inventory_index as file_inventory_index_service
from app.services.maintenance_context import as_ctx
from app.core import profiling

_RESTORE_STAMP_SUFFIX_RE = re.compile(r"_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_\d+)?$")
_INDEX_LOCK = threading.Lock()
_DIR_SIZE_CACHE = {}



def _backup_bucket(name):
    """Classify a backup filename into its retention bucket."""
    lowered = (name or "").lower()
    if "_pre_restore" in lowered:
        return "pre_restore"
    if "_auto" in lowered:
        return "auto"
    if "_session_end" in lowered:
        return "session"
    if "_emergency" in lowered:
        return "emergency"
    if "_manual" in lowered:
        return "manual"
    return "other"


def _iter_backup_files(backup_dir):
    """Iter backup files."""
    if not backup_dir.exists() or not backup_dir.is_dir():
        return []
    items = []
    for path in backup_dir.glob("*.zip"):
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({
            "path": path,
            "name": path.name,
            "mtime": float(stat.st_mtime),
            "size": int(stat.st_size),
            "bucket": _backup_bucket(path.name),
        })
    return items


def _safe_dir_mtime_ns(path):
    try:
        return int(Path(path).stat().st_mtime_ns)
    except OSError:
        return -1


def _cleanup_backups(backup_dir, *, keep_manual, keep_other, keep_auto_days, keep_session_days, keep_pre_restore_days, dry_run):
    """Apply retention rules to backup archives and return a preview/result payload."""
    now = time.time()
    files = _iter_backup_files(backup_dir)
    by_bucket = {"manual": [], "emergency": [], "other": [], "auto": [], "session": [], "pre_restore": []}
    for item in files:
        by_bucket[item["bucket"]].append(item)
    for bucket in by_bucket:
        by_bucket[bucket].sort(key=lambda row: row["mtime"], reverse=True)

    to_delete = []
    for idx, item in enumerate(by_bucket["manual"]):
        if idx >= keep_manual:
            to_delete.append(item)
    for idx, item in enumerate(by_bucket["other"]):
        if idx >= keep_other:
            to_delete.append(item)

    auto_cutoff = now - (keep_auto_days * 86400)
    for item in by_bucket["auto"]:
        if item["mtime"] < auto_cutoff:
            to_delete.append(item)

    session_cutoff = now - (keep_session_days * 86400)
    for item in by_bucket["session"]:
        if item["mtime"] < session_cutoff:
            to_delete.append(item)

    prerestore_cutoff = now - (keep_pre_restore_days * 86400)
    for item in by_bucket["pre_restore"]:
        if item["mtime"] < prerestore_cutoff:
            to_delete.append(item)

    unique = {str(item["path"]): item for item in to_delete}
    targets = sorted(unique.values(), key=lambda row: row["mtime"])

    deleted = []
    errors = []
    for item in targets:
        if dry_run:
            deleted.append(item)
            continue
        try:
            item["path"].unlink(missing_ok=True)
            deleted.append(item)
        except OSError as exc:
            errors.append(f"{item['name']}: {exc}")

    target_paths = {str(item["path"]) for item in targets}
    preview_items = [
        {
            "name": item["name"],
            "bucket": item["bucket"],
            "mtime": item["mtime"],
            "size": item["size"],
            "deletable": str(item["path"]) in target_paths,
        }
        for item in sorted(files, key=lambda row: row["mtime"], reverse=True)
    ]

    return {
        "total": len(files),
        "matched": len(targets),
        "deleted": len(deleted),
        "deleted_size": sum(item["size"] for item in deleted),
        "errors": errors,
        "dry_run": bool(dry_run),
        "items": preview_items,
    }


def _iter_old_world_dirs(data_dir):
    """Iter old world dirs."""
    old_worlds_dir = data_dir / "old_worlds"
    if not old_worlds_dir.exists() or not old_worlds_dir.is_dir():
        return []
    return [child for child in old_worlds_dir.iterdir() if child.is_dir()]


def _cleanup_stale_worlds(*, world_dir, data_dir, keep_count, max_age_days, dry_run):
    """Prune archived world directories outside the active world and retention window."""
    now = time.time()
    world_dir = Path(world_dir).resolve()
    old_worlds_dir = data_dir / "old_worlds"
    cutoff = now - (max_age_days * 86400)
    stale_paths = []
    for old_path in _iter_old_world_dirs(data_dir):
        try:
            resolved = old_path.resolve()
        except OSError:
            continue
        if not resolved.exists() or not resolved.is_dir():
            continue
        if resolved == world_dir or resolved.parent != old_worlds_dir.resolve():
            continue
        if not _RESTORE_STAMP_SUFFIX_RE.search(resolved.name):
            continue
        try:
            stat = resolved.stat()
            mtime = float(stat.st_mtime)
            size_bytes = 0
            for root, _, files in os.walk(resolved):
                for file_name in files:
                    try:
                        size_bytes += int((Path(root) / file_name).stat().st_size)
                    except OSError:
                        continue
        except OSError:
            continue
        stale_paths.append({"path": resolved, "name": resolved.name, "mtime": mtime, "size": size_bytes})

    stale_paths.sort(key=lambda row: row["mtime"], reverse=True)
    delete_targets = [item for idx, item in enumerate(stale_paths) if idx >= keep_count and item["mtime"] <= cutoff]

    deleted = []
    errors = []
    for item in delete_targets:
        if dry_run:
            deleted.append(item)
            continue
        try:
            ports.filesystem.rmtree(item["path"])
            deleted.append(item)
        except OSError as exc:
            errors.append(f"{item['name']}: {exc}")

    target_paths = {str(item["path"]) for item in delete_targets}
    preview_items = [
        {
            "name": item["name"],
            "mtime": item["mtime"],
            "size": item["size"],
            "deletable": str(item["path"]) in target_paths,
        }
        for item in stale_paths
    ]

    return {
        "total_candidates": len(stale_paths),
        "matched": len(delete_targets),
        "deleted": len(deleted),
        "errors": errors,
        "dry_run": bool(dry_run),
        "items": preview_items,
    }


def _cleanup_is_under(root, path):
    """Return whether a resolved path stays within the resolved root."""
    root = Path(root).resolve()
    path = Path(path).resolve()
    return path == root or root in path.parents


def _cleanup_read_level_name(path):
    """Read the configured level-name from a server.properties file."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "level-name":
            value = value.strip()
            return value or None
    return None


def _cleanup_active_world_path(ctx):
    """Resolve the currently active world path from server.properties candidates."""
    ctx = as_ctx(ctx)
    for candidate in ctx.SERVER_PROPERTIES_CANDIDATES:
        if not Path(candidate).exists():
            continue
        level_name = _cleanup_read_level_name(candidate)
        if not level_name:
            continue
        path = Path(level_name)
        if not path.is_absolute():
            path = Path(candidate).parent / path
        try:
            return path.resolve()
        except OSError:
            return None
    return None


def _cleanup_dir_size(path):
    """Return a cached recursive directory size for cleanup previews."""
    with profiling.timed("maintenance.fs.dir_size"):
        target_path = Path(path)
        key = str(target_path.resolve())
        mtime_ns = _safe_dir_mtime_ns(target_path)
        with _INDEX_LOCK:
            cached = _DIR_SIZE_CACHE.get(key)
            if isinstance(cached, dict) and int(cached.get("mtime_ns", -1)) == mtime_ns:
                return int(cached.get("size", 0))
        total = 0
        for root, _, files in os.walk(path):
            for name in files:
                target = Path(root) / name
                try:
                    total += int(target.stat().st_size)
                except OSError:
                    continue
        with _INDEX_LOCK:
            _DIR_SIZE_CACHE[key] = {"mtime_ns": mtime_ns, "size": int(total)}
        return total


def _cleanup_collect_candidates(ctx, cfg):
    """Collect backup and world cleanup candidates for maintenance evaluation."""
    ctx = as_ctx(ctx)
    with profiling.timed("maintenance.candidate_discovery.total"):
        backup_dir = Path(ctx.BACKUP_DIR).resolve()
        data_dir = _cleanup_data_dir(ctx).resolve()
        old_worlds_dir = (data_dir / "old_worlds").resolve()
        snapshot_root = Path(getattr(ctx, "AUTO_SNAPSHOT_DIR", "") or (backup_dir / "snapshots"))
        inventory = file_inventory_index_service.get_inventory(
            backup_root=backup_dir,
            snapshot_root=snapshot_root,
            old_worlds_root=old_worlds_dir,
        )
        allowed_roots = [backup_dir, old_worlds_dir]
        with profiling.timed("maintenance.candidate_discovery.active_world_probe"):
            active_world = _cleanup_active_world_path(ctx)
        categories = cfg.get("rules", {}).get("categories", {})
        candidates = []

        def _append(path, category, is_dir=False):
            """Append one discovered candidate row with eligibility guards."""
            row = {
                "category": category,
                "path": str(path),
                "name": Path(path).name,
                "is_dir": bool(is_dir),
                "size": 0,
                "mtime": 0.0,
                "eligible": True,
                "reasons": [],
            }
            try:
                stat = Path(path).stat()
                row["mtime"] = float(stat.st_mtime)
                row["size"] = _cleanup_dir_size(path) if is_dir else int(stat.st_size)
            except OSError:
                row["eligible"] = False
                row["reasons"].append("stat_failed")

            resolved = None
            try:
                resolved = Path(path).resolve()
            except OSError:
                row["eligible"] = False
                row["reasons"].append("resolve_failed")

            if resolved is not None:
                if Path(path).is_symlink():
                    row["eligible"] = False
                    row["reasons"].append("symlink_blocked")
                if not any(_cleanup_is_under(root, resolved) for root in allowed_roots):
                    row["eligible"] = False
                    row["reasons"].append("outside_allowed_roots")
                if active_world is not None and (resolved == active_world or active_world in resolved.parents or resolved in active_world.parents):
                    row["eligible"] = False
                    row["reasons"].append("active_world_protected")

            if not categories.get(category, False):
                row["eligible"] = False
                row["reasons"].append("category_disabled")
            candidates.append(row)

        if backup_dir.exists() and backup_dir.is_dir():
            with profiling.timed("maintenance.candidate_discovery.scan_backup_dir"):
                for entry in inventory.get("backup_zip_paths", []):
                    _append(entry, "backup_zip", is_dir=False)

        if old_worlds_dir.exists() and old_worlds_dir.is_dir():
            with profiling.timed("maintenance.candidate_discovery.scan_old_worlds"):
                top_entries = inventory.get("old_world_top_entries", [])
                nested_zip_paths = inventory.get("old_world_nested_zip_paths", [])
                for entry in top_entries:
                    if entry.is_dir():
                        _append(entry, "stale_world_dir", is_dir=True)
                    elif entry.is_file() and entry.suffix.lower() == ".zip":
                        _append(entry, "old_world_zip", is_dir=False)
                for entry in nested_zip_paths:
                    _append(entry, "old_world_zip", is_dir=False)

        candidates.sort(key=lambda row: row["mtime"], reverse=True)
        profiling.set_gauge("maintenance.candidate_count", len(candidates))
        return candidates



