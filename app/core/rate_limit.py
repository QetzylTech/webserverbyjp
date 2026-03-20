"""In-memory fixed-window rate limit helpers for route backpressure."""

from __future__ import annotations

import threading
import time


class InMemoryRateLimiter:
    """Simple per-key fixed-window limiter suitable for single-process apps."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, dict[str, float | int]] = {}

    def allow(self, key: object, *, limit: int, window_seconds: float) -> tuple[bool, int]:
        now = time.time()
        bucket_key = str(key or "")
        max_hits = max(1, int(limit))
        window = max(0.1, float(window_seconds))
        with self._lock:
            bucket = self._buckets.get(bucket_key)
            if not isinstance(bucket, dict) or float(bucket.get("reset_at", 0.0)) <= now:
                bucket = {"count": 0, "reset_at": now + window}
                self._buckets[bucket_key] = bucket
            if int(bucket.get("count", 0)) >= max_hits:
                retry_after = max(1, int(round(float(bucket["reset_at"]) - now)))
                return False, retry_after
            bucket["count"] = int(bucket.get("count", 0)) + 1
            return True, 0
