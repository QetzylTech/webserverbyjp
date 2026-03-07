"""Expose system metric readers through the selected platform ports."""
from app.ports import ports


def get_cpu_usage_per_core():
    """Return per-core CPU utilization percentages as strings."""
    return ports.metrics.get_cpu_usage_per_core()


def get_ram_usage():
    """Return RAM usage summary."""
    return ports.metrics.get_ram_usage()


def get_cpu_frequency():
    """Return average CPU frequency."""
    return ports.metrics.get_cpu_frequency()


def get_storage_usage():
    """Return root filesystem usage summary."""
    return ports.metrics.get_storage_usage()

