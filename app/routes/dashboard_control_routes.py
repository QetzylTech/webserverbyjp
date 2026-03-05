"""HTTP translation layer for control routes."""

import threading

from app.commands.control_commands import register_control_routes as _register_control_routes


def register_control_routes(app, state, *, run_cleanup_event_if_enabled):
    """Register start/stop/backup/restore/RCON routes via command handlers."""
    return _register_control_routes(
        app,
        state,
        run_cleanup_event_if_enabled=run_cleanup_event_if_enabled,
        threading_module=threading,
    )
