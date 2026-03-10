"""Command helpers for snapshot archive downloads."""

from __future__ import annotations

from app.services import snapshot_archive as snapshot_archive_service


def build_snapshot_archive(snapshot_dir, safe_name):
    """Create a snapshot zip archive and return (zip_path, tmp_root)."""
    return snapshot_archive_service.build_snapshot_archive(snapshot_dir, safe_name)


def cleanup_snapshot_archive(tmp_root):
    """Remove temporary snapshot archive directories."""
    snapshot_archive_service.cleanup_snapshot_archive(tmp_root)
