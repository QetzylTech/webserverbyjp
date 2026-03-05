"""Worker bootstrap entrypoints."""

from __future__ import annotations

from app.bootstrap import web_app


def run_worker():
    """Run worker loops using composed runtime state."""
    return web_app.run_worker()
