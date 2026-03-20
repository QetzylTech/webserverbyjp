"""Explicit AppState builder."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.state import AppState, REQUIRED_STATE_KEYS


def assert_required_keys_present(bindings: Mapping[str, Any]) -> None:
    """Raise when any required AppState members are missing from bindings."""
    required = REQUIRED_STATE_KEYS
    missing = [key for key in required if key not in bindings]
    if missing:
        raise KeyError(f"Missing state members: {', '.join(missing)}")


def build_app_state(bindings: Mapping[str, Any]) -> AppState:
    """Build AppState from explicit bindings using annotated keys."""
    assert_required_keys_present(bindings)
    required = REQUIRED_STATE_KEYS
    data: dict[str, Any] = {}
    for key in required:
        data[key] = bindings[key]
    return AppState(data)
