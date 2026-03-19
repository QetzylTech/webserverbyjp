"""Password retry throttling helpers."""

import time


def _get_state(ctx):
    lock = getattr(ctx, "password_throttle_lock", None)
    state = getattr(ctx, "password_throttle_state", None)
    if lock is None or state is None:
        return None, None
    return lock, state


def _get_entry(state, client_ip):
    key = str(client_ip or "unknown").strip() or "unknown"
    entry = state.get("by_ip", {}).get(key)
    if not isinstance(entry, dict):
        entry = {"count": 0, "blocked_until": 0.0}
        state.setdefault("by_ip", {})[key] = entry
    return key, entry


def is_blocked(ctx, client_ip):
    lock, state = _get_state(ctx)
    if lock is None or state is None:
        return False
    now = time.time()
    with lock:
        _key, entry = _get_entry(state, client_ip)
        blocked_until = float(entry.get("blocked_until", 0.0) or 0.0)
        if blocked_until and blocked_until > now:
            return True
        if blocked_until and blocked_until <= now:
            entry["blocked_until"] = 0.0
            entry["count"] = 0
    return False


def record_failure(ctx, client_ip, *, max_attempts=3, block_seconds=10):
    lock, state = _get_state(ctx)
    if lock is None or state is None:
        return 0.0, False
    now = time.time()
    with lock:
        _key, entry = _get_entry(state, client_ip)
        blocked_until = float(entry.get("blocked_until", 0.0) or 0.0)
        if blocked_until and blocked_until > now:
            return blocked_until, False
        count = int(entry.get("count", 0) or 0) + 1
        entry["count"] = count
        if count >= max_attempts:
            entry["blocked_until"] = now + float(block_seconds)
            entry["count"] = 0
            return entry["blocked_until"], True
    return 0.0, False


def record_success(ctx, client_ip):
    lock, state = _get_state(ctx)
    if lock is None or state is None:
        return
    with lock:
        _key, entry = _get_entry(state, client_ip)
        entry["count"] = 0
        entry["blocked_until"] = 0.0
