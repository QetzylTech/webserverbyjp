"""Snapshot archive creation utilities."""

from __future__ import annotations

import time
import tracemalloc
from pathlib import Path

from app.core import profiling
from app.ports import ports


def build_snapshot_archive(snapshot_dir: Path, safe_name: str) -> tuple[Path, Path]:
    """Zip a snapshot directory into a temporary archive and return paths."""
    tmp_root = Path(ports.filesystem.mkdtemp(prefix="mcweb_snapshot_zip_"))
    tracemalloc_started = False
    if profiling.ENABLED and not tracemalloc.is_tracing():
        tracemalloc.start()
        tracemalloc_started = True
    started = time.perf_counter()
    zip_path = Path(ports.filesystem.make_zip_archive(tmp_root / safe_name, root_dir=snapshot_dir))
    elapsed = time.perf_counter() - started
    profiling.record_duration("snapshot_download.zip_build", elapsed)
    if profiling.ENABLED and tracemalloc.is_tracing():
        _current, peak = tracemalloc.get_traced_memory()
        profiling.set_gauge("snapshot_download.zip_peak_bytes", int(peak))
        if tracemalloc_started:
            tracemalloc.stop()
    return zip_path, tmp_root


def cleanup_snapshot_archive(tmp_root: Path) -> None:
    """Remove temporary snapshot archive directories."""
    try:
        ports.filesystem.rmtree(tmp_root, ignore_errors=True)
    except OSError:
        pass
