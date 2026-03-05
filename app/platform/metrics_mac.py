from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _format_bytes(value):
    units = ["B", "K", "M", "G", "T", "P"]
    v = float(max(0, value))
    idx = 0
    while v >= 1024 and idx < len(units) - 1:
        v /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(v)}{units[idx]}"
    return f"{v:.1f}{units[idx]}"


def _run(cmd, *, timeout=3):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def get_cpu_usage_per_core():
    try:
        import psutil  # type: ignore

        values = psutil.cpu_percent(interval=0.15, percpu=True)
        if values:
            return [f"{float(v):.1f}" for v in values]
    except Exception:
        pass
    return ["unknown"]


def get_ram_usage():
    try:
        import psutil  # type: ignore

        mem = psutil.virtual_memory()
        used = int(mem.total - mem.available)
        return f"{used / (1024 ** 3):.2f} / {mem.total / (1024 ** 3):.2f} GB ({float(mem.percent):.1f}%)"
    except Exception:
        pass
    return "unknown"


def get_cpu_frequency():
    try:
        import psutil  # type: ignore

        freq = psutil.cpu_freq()
        if freq and freq.current:
            return f"{(float(freq.current) / 1000.0):.2f} GHz"
    except Exception:
        pass
    raw = _run(["sysctl", "-n", "hw.cpufrequency"])
    if not raw:
        return "unknown"
    try:
        hz = float(raw)
    except ValueError:
        return "unknown"
    if hz <= 0:
        return "unknown"
    return f"{(hz / 1_000_000_000.0):.2f} GHz"


def get_storage_usage():
    try:
        usage = shutil.disk_usage(Path.cwd().anchor or "/")
        total = int(usage.total)
        available = int(usage.free)
    except (OSError, AttributeError):
        return "unknown"
    used = total - available
    if total <= 0:
        return "unknown"
    percent = (used / total) * 100.0
    return f"{_format_bytes(used)} / {_format_bytes(total)} ({percent:.0f}%)"
