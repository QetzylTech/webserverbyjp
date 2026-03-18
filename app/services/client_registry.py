"""In-memory client registry for activity and cadence tracking."""

from __future__ import annotations

import time


def _registry_state(ctx):
    registry = getattr(ctx, "client_registry", None)
    lock = getattr(ctx, "client_registry_lock", None)
    if registry is None or lock is None:
        return {}, None
    return registry, lock


def _client_ttl_seconds(ctx):
    ttl_candidates = []
    try:
        ttl_candidates.append(float(getattr(ctx, "HOME_PAGE_ACTIVE_TTL_SECONDS", 0.0) or 0.0))
    except Exception:
        pass
    try:
        ttl_candidates.append(float(getattr(ctx, "FILE_PAGE_ACTIVE_TTL_SECONDS", 0.0) or 0.0))
    except Exception:
        pass
    try:
        ttl_candidates.append(float(getattr(ctx, "METRICS_STREAM_HEARTBEAT_SECONDS", 0.0) or 0.0) * 2.5)
    except Exception:
        pass
    ttl = max([item for item in ttl_candidates if item and item > 0.0] or [15.0])
    return max(10.0, ttl)


def _touch_entry(entry, now, channel):
    entry["last_seen"] = now
    if channel:
        entry.setdefault("channels", set()).add(channel)


def register_client(ctx, client_id, *, channel=""):
    """Register a client in the registry for the given channel."""
    client_id = str(client_id or "").strip()
    if not client_id:
        return False
    registry, lock = _registry_state(ctx)
    if lock is None:
        return False
    now = time.time()
    with lock:
        entry = registry.get(client_id)
        if not isinstance(entry, dict):
            entry = {"last_seen": now, "channels": set()}
        _touch_entry(entry, now, channel)
        registry[client_id] = entry
    return True


def touch_client(ctx, client_id, *, channel=""):
    """Update the last-seen timestamp for a client."""
    return register_client(ctx, client_id, channel=channel)


def unregister_client(ctx, client_id, *, channel=""):
    """Remove a client or channel from the registry."""
    client_id = str(client_id or "").strip()
    if not client_id:
        return False
    registry, lock = _registry_state(ctx)
    if lock is None:
        return False
    with lock:
        entry = registry.get(client_id)
        if not isinstance(entry, dict):
            return False
        if channel:
            channels = entry.get("channels")
            if isinstance(channels, set) and channel in channels:
                channels.discard(channel)
        channels = entry.get("channels")
        if not channels:
            registry.pop(client_id, None)
            return True
        entry["last_seen"] = time.time()
        registry[client_id] = entry
    return True


def prune_inactive_clients(ctx):
    """Remove clients that have not been seen within the TTL window."""
    registry, lock = _registry_state(ctx)
    if lock is None:
        return 0
    now = time.time()
    ttl = _client_ttl_seconds(ctx)
    removed = 0
    with lock:
        stale = [key for key, entry in registry.items() if now - float(entry.get("last_seen", 0.0) or 0.0) > ttl]
        for key in stale:
            registry.pop(key, None)
            removed += 1
    return removed


def active_client_count(ctx):
    """Return count of active clients after pruning stale entries."""
    registry, lock = _registry_state(ctx)
    if lock is None:
        return 0
    prune_inactive_clients(ctx)
    with lock:
        return len(registry)
