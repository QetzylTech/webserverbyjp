"""Device name mapping helpers."""

from app.core import state_store as state_store_service


def get_device_name_map(csv_path, cache_lock, cache, cache_mtime_ns, log_exception, app_state_db_path=None):
    """Return cached IP -> device-name map from SQLite."""
    try:
        db_mtime_ns = app_state_db_path.stat().st_mtime_ns if app_state_db_path else None
    except OSError:
        db_mtime_ns = None
    cache_token = (db_mtime_ns,)
    with cache_lock:
        if cache_mtime_ns[0] == cache_token:
            return dict(cache)

    mapping = {}
    fallmap = {}
    if app_state_db_path:
        try:
            fallmap = state_store_service.load_fallmap(app_state_db_path)
        except Exception as exc:
            log_exception("device_name_map_load/fallmap_db", exc)
            fallmap = {}
    for ip, name in fallmap.items():
        mapping[ip] = name

    with cache_lock:
        cache.clear()
        cache.update(mapping)
        cache_mtime_ns[0] = cache_token
    return mapping
