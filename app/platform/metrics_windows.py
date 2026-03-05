from __future__ import annotations

import ctypes
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


def _run_powershell(script, timeout=3):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def get_cpu_usage_per_core():
    try:
        import psutil  # type: ignore

        values = psutil.cpu_percent(interval=0.15, percpu=True)
        if values:
            return [f"{float(v):.1f}" for v in values]
    except Exception:
        pass
    try:
        lines = _run_powershell(
            "$c=(Get-Counter '\\Processor(*)\\% Processor Time').CounterSamples;"
            "$c|?{$_.InstanceName -match '^[0-9]+$'}|sort InstanceName|"
            "%%{[math]::Round($_.CookedValue,1)}",
            timeout=4,
        )
        if lines:
            return [str(float(line)) for line in lines]
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
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        memory_status = MEMORYSTATUSEX()
        memory_status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory_status)):
            return "unknown"
        total = int(memory_status.ullTotalPhys)
        avail = int(memory_status.ullAvailPhys)
        used = max(0, total - avail)
        percent = (used / total) * 100.0 if total > 0 else 0.0
        return f"{used / (1024 ** 3):.2f} / {total / (1024 ** 3):.2f} GB ({percent:.1f}%)"
    except Exception:
        return "unknown"


def get_cpu_frequency():
    try:
        import psutil  # type: ignore

        freq = psutil.cpu_freq()
        if freq and freq.current:
            return f"{(float(freq.current) / 1000.0):.2f} GHz"
    except Exception:
        pass
    try:
        lines = _run_powershell(
            "(Get-CimInstance Win32_Processor | Measure-Object -Property CurrentClockSpeed -Average).Average",
            timeout=3,
        )
        if lines:
            mhz = float(lines[-1])
            if mhz > 0:
                return f"{(mhz / 1000.0):.2f} GHz"
    except Exception:
        pass
    return "unknown"


def get_storage_usage():
    try:
        usage = shutil.disk_usage(Path.cwd().anchor or "C:\\")
        total = int(usage.total)
        available = int(usage.free)
    except (OSError, AttributeError):
        return "unknown"
    used = total - available
    if total <= 0:
        return "unknown"
    percent = (used / total) * 100.0
    return f"{_format_bytes(used)} / {_format_bytes(total)} ({percent:.0f}%)"
