"""Shared types for control-plane command handlers."""

from dataclasses import dataclass
import threading
from typing import Any


@dataclass(frozen=True)
class ControlCommandContext:
    state: object
    process_role: str
    run_cleanup_event_if_enabled: Any
    threading_module: Any = threading


@dataclass(frozen=True)
class CommandResult:
    payload: dict[str, Any] | None = None
    status_code: int = 200
    headers: dict[str, Any] | None = None
    response: object | None = None
