"""Service status cache helpers."""
import subprocess
import time


def get_status(
    *,
    cache_lock,
    cache_value_ref,
    cache_at_ref,
    service,
    active_ttl_seconds,
    off_ttl_seconds,
    timeout_seconds,
    log_action,
    log_exception,
):
        # Return cached or freshly queried systemd service status.
    now = time.time()
    with cache_lock:
        cached = cache_value_ref[0]
        cached_at = cache_at_ref[0]
    if cached:
        ttl = active_ttl_seconds if cached == "active" else off_ttl_seconds
        if ttl > 0 and (now - cached_at) <= ttl:
            return cached

    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        status = result.stdout.strip() or "unknown"
    except subprocess.TimeoutExpired:
        log_action(
            "status-timeout",
            command=f"systemctl is-active {service}",
            rejection_message=f"Timed out after {timeout_seconds:.1f}s.",
        )
        status = "unknown"
    except Exception as exc:
        log_exception("get_status", exc)
        status = "unknown"

    with cache_lock:
        cache_value_ref[0] = status
        cache_at_ref[0] = now
    return status


def invalidate_status_cache(cache_lock, cache_value_ref, cache_at_ref):
    """Reset cached service status value/time."""
    with cache_lock:
        cache_value_ref[0] = ""
        cache_at_ref[0] = 0.0

