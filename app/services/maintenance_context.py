"""Shared maintenance-service helpers for working with runtime context objects."""


class MappingCtx:
    """Expose dict values through attribute access for maintenance helpers."""

    def __init__(self, data):
        self._data = data if isinstance(data, dict) else {}

    def __getattr__(self, name):
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)


def as_ctx(value):
    """Return the underlying runtime context for state wrappers or plain mappings."""
    if hasattr(value, "ctx"):
        return value.ctx
    if isinstance(value, dict):
        return MappingCtx(value)
    return value
