from __future__ import annotations

import platform as py_platform
from importlib import import_module
from types import ModuleType

_CALLS: ModuleType | None = None
_PLATFORM_NAME = ""
_PATHS: ModuleType | None = None
_METRICS: ModuleType | None = None


def _detect_module_name() -> tuple[str, str]:
    system_name = (py_platform.system() or "").strip().lower()
    if system_name == "windows":
        return "app.platform.calls_windows", "windows"
    if system_name == "darwin":
        return "app.platform.calls_mac", "mac"
    return "app.platform.calls_linux_deb", "linux"


def _detect_paths_module_name() -> tuple[str, str]:
    system_name = (py_platform.system() or "").strip().lower()
    if system_name == "windows":
        return "app.platform.paths_windows", "windows"
    if system_name == "darwin":
        return "app.platform.paths_mac", "mac"
    return "app.platform.paths_linux", "linux"


def _detect_metrics_module_name() -> tuple[str, str]:
    system_name = (py_platform.system() or "").strip().lower()
    if system_name == "windows":
        return "app.platform.metrics_windows", "windows"
    if system_name == "darwin":
        return "app.platform.metrics_mac", "mac"
    return "app.platform.metrics_linux_deb", "linux"


def get_calls() -> ModuleType:
    global _CALLS
    global _PLATFORM_NAME
    if _CALLS is not None:
        return _CALLS
    module_name, short_name = _detect_module_name()
    _CALLS = import_module(module_name)
    _PLATFORM_NAME = short_name
    return _CALLS


def get_paths() -> ModuleType:
    global _PATHS
    global _PLATFORM_NAME
    if _PATHS is not None:
        return _PATHS
    module_name, short_name = _detect_paths_module_name()
    _PATHS = import_module(module_name)
    if not _PLATFORM_NAME:
        _PLATFORM_NAME = short_name
    return _PATHS


def get_platform_name() -> str:
    if not _PLATFORM_NAME:
        get_calls()
    return _PLATFORM_NAME


def get_metrics() -> ModuleType:
    global _METRICS
    global _PLATFORM_NAME
    if _METRICS is not None:
        return _METRICS
    module_name, short_name = _detect_metrics_module_name()
    _METRICS = import_module(module_name)
    if not _PLATFORM_NAME:
        _PLATFORM_NAME = short_name
    return _METRICS
