"""Backward-compatible shim for dashboard runtime query helpers."""

from app.queries import dashboard_runtime_queries as _queries

__all__ = [name for name in dir(_queries) if not name.startswith('__')]


def __getattr__(name):
    return getattr(_queries, name)
