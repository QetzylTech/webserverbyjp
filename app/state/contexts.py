"""Typed runtime context containers.

These contexts split application state into:
- ConfigContext: immutable env/config-derived values.
- RuntimeContext: mutable in-memory locks/caches/state.
- ServicePorts: callable integrations/adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ConfigContext:
    """Immutable configuration values exposed via attribute access."""

    values: Mapping[str, Any]

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


@dataclass
class RuntimeContext:
    """Mutable runtime state exposed via attribute access."""

    values: dict[str, Any]

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "values":
            object.__setattr__(self, name, value)
            return
        self.values[name] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


@dataclass(frozen=True)
class ServicePorts:
    """Callable service/infrastructure ports exposed via attribute access."""

    values: Mapping[str, Any]

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


class UnifiedServiceContext:
    """Compatibility adapter over typed contexts.

    Existing services still expecting a single ``ctx`` object can read attributes
    from ports/runtime/config in that order, while writes are limited to runtime.
    """

    __slots__ = ("config", "runtime", "ports")

    def __init__(self, config: ConfigContext, runtime: RuntimeContext, ports: ServicePorts):
        object.__setattr__(self, "config", config)
        object.__setattr__(self, "runtime", runtime)
        object.__setattr__(self, "ports", ports)

    def __getattr__(self, name: str) -> Any:
        for source in (self.ports, self.runtime, self.config):
            try:
                return getattr(source, name)
            except AttributeError:
                continue
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self.runtime, name, value)
