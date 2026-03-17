"""Shared types for control-plane command handlers."""

from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class ControlCommandContext:
    state: object
    process_role: str
    run_cleanup_event_if_enabled: object
    threading_module: object = threading


@dataclass(frozen=True)
class CommandResult:
    payload: dict | None = None
    status_code: int = 200
    headers: dict | None = None
    response: object | None = None
