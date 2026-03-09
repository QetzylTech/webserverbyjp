"""Shared dashboard page-activity helpers used by cadence workers."""

import time


def mark_home_page_client_active(ctx):
    """Record recent home-page activity and wake cadence workers."""
    with ctx.metrics_cache_cond:
        ctx.home_page_last_seen = time.time()
        ctx.metrics_cache_cond.notify_all()


def mark_file_page_client_active(ctx):
    """Record recent file-page activity and wake cadence workers."""
    with ctx.metrics_cache_cond:
        ctx.file_page_last_seen = time.time()
        ctx.metrics_cache_cond.notify_all()


def has_active_home_page_clients(ctx):
    """Return whether home-page activity is still within the active TTL."""
    with ctx.metrics_cache_cond:
        last_seen = float(getattr(ctx, "home_page_last_seen", 0.0) or 0.0)
        ttl_seconds = float(getattr(ctx, "HOME_PAGE_ACTIVE_TTL_SECONDS", 0.0) or 0.0)
    return (time.time() - last_seen) <= ttl_seconds


def has_active_file_page_clients(ctx):
    """Return whether file-page activity is still within the active TTL."""
    with ctx.metrics_cache_cond:
        last_seen = float(getattr(ctx, "file_page_last_seen", 0.0) or 0.0)
        ttl_seconds = float(getattr(ctx, "FILE_PAGE_ACTIVE_TTL_SECONDS", 0.0) or 0.0)
    return (time.time() - last_seen) <= ttl_seconds


def has_active_flask_app_clients(ctx):
    """Return whether any shell page or SSE stream is actively consuming data."""
    now = time.time()
    with ctx.metrics_cache_cond:
        stream_clients = int(getattr(ctx, "metrics_stream_client_count", 0) or 0)
        home_last_seen = float(getattr(ctx, "home_page_last_seen", 0.0) or 0.0)
        file_last_seen = float(getattr(ctx, "file_page_last_seen", 0.0) or 0.0)
        home_ttl_seconds = float(getattr(ctx, "HOME_PAGE_ACTIVE_TTL_SECONDS", 0.0) or 0.0)
        file_ttl_seconds = float(getattr(ctx, "FILE_PAGE_ACTIVE_TTL_SECONDS", 0.0) or 0.0)
    return (
        stream_clients > 0
        or (now - home_last_seen) <= home_ttl_seconds
        or (now - file_last_seen) <= file_ttl_seconds
    )
