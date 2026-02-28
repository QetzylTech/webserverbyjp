"""System metric probes backed by Linux procfs/sysfs."""

import os
import time
from pathlib import Path


def _read_proc_stat():
    """Read CPU lines from ``/proc/stat`` for utilization sampling."""
    with open("/proc/stat", "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.startswith("cpu")]


def _parse_cpu_times(line):
    """Parse one ``/proc/stat`` CPU line into (total, idle) jiffies."""
    parts = line.split()
    values = [int(v) for v in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total, idle


def get_cpu_usage_per_core():
    """Return per-core CPU utilization percentages as strings."""
    # Sample twice to compute deltas instead of using a single absolute snapshot.
    first = _read_proc_stat()
    time.sleep(0.15)
    second = _read_proc_stat()

    usages = []
    for i in range(1, min(len(first), len(second))):
        total1, idle1 = _parse_cpu_times(first[i])
        total2, idle2 = _parse_cpu_times(second[i])
        total_delta = total2 - total1
        idle_delta = idle2 - idle1
        if total_delta <= 0:
            usages.append("0.0")
            continue
        usage = 100.0 * (1.0 - (idle_delta / total_delta))
        usages.append(f"{usage:.1f}")
    return usages


def get_ram_usage():
    """Return RAM usage summary from ``/proc/meminfo``."""
    mem_total_kb = 0
    mem_available_kb = 0
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                mem_total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available_kb = int(line.split()[1])

    if mem_total_kb <= 0:
        return "unknown"

    used_kb = mem_total_kb - mem_available_kb
    used_gb = used_kb / (1024 * 1024)
    total_gb = mem_total_kb / (1024 * 1024)
    percent = (used_kb / mem_total_kb) * 100.0
    return f"{used_gb:.2f} / {total_gb:.2f} GB ({percent:.1f}%)"


def get_cpu_frequency():
    """Return average CPU frequency, preferring cpufreq then cpuinfo fallback."""
    freq_paths = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq"))
    freqs_khz = []
    for path in freq_paths:
        try:
            value = path.read_text(encoding="utf-8").strip()
            freqs_khz.append(int(value))
        except (ValueError, OSError):
            continue

    if freqs_khz:
        avg_ghz = (sum(freqs_khz) / len(freqs_khz)) / 1_000_000
        return f"{avg_ghz:.2f} GHz"

    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            mhz_values = []
            for line in f:
                if line.lower().startswith("cpu mhz"):
                    mhz_values.append(float(line.split(":", 1)[1].strip()))
        if mhz_values:
            avg_ghz = (sum(mhz_values) / len(mhz_values)) / 1000
            return f"{avg_ghz:.2f} GHz"
    except OSError:
        pass

    return "unknown"


def get_storage_usage():
    """Return root filesystem usage summary from ``statvfs``."""
    try:
        stat = os.statvfs("/")
    except OSError:
        return "unknown"
    total = stat.f_blocks * stat.f_frsize
    available = stat.f_bavail * stat.f_frsize
    used = total - available
    if total <= 0:
        return "unknown"
    percent = (used / total) * 100.0

    def _fmt(value):
        units = ["B", "K", "M", "G", "T", "P"]
        v = float(max(0, value))
        idx = 0
        while v >= 1024 and idx < len(units) - 1:
            v /= 1024.0
            idx += 1
        if idx == 0:
            return f"{int(v)}{units[idx]}"
        return f"{v:.1f}{units[idx]}"

    return f"{_fmt(used)} / {_fmt(total)} ({percent:.0f}%)"
