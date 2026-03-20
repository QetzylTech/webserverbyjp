"""Shared maintenance-service helpers for working with runtime context objects."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class MappingCtx:
    """Expose dict values through attribute access for maintenance helpers."""

    def __init__(self, data: Mapping[str, Any] | dict[str, Any] | None) -> None:
        self._data: dict[str, Any] = dict(data) if isinstance(data, Mapping) else {}

    def __getattr__(self, name: str) -> Any:
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)


def as_ctx(value: Any) -> Any:
    """Return the underlying runtime context for state wrappers or plain mappings."""
    if hasattr(value, "ctx"):
        return value.ctx
    if isinstance(value, Mapping):
        return MappingCtx(value)
    return value
