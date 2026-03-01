"""Device name mapping helpers."""
import csv

from app.core import state_store as state_store_service


def get_device_name_map(csv_path, cache_lock, cache, cache_mtime_ns, log_exception, app_state_db_path=None):
    """Return cached IP -> device-name map (CSV primary, SQLite map augment)."""
    try:
        current_mtime_ns = csv_path.stat().st_mtime_ns
    except OSError:
        current_mtime_ns = None
    try:
        db_mtime_ns = app_state_db_path.stat().st_mtime_ns if app_state_db_path else None
    except OSError:
        db_mtime_ns = None
    cache_token = (current_mtime_ns, db_mtime_ns)
    with cache_lock:
        if cache_mtime_ns[0] == cache_token:
            return dict(cache)

    mapping = {}
    if current_mtime_ns is not None:
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    if not isinstance(row, dict):
                        continue
                    name = str(row.get("Device name", "") or "").strip()
                    ips_text = str(row.get("Tailscale IPs", "") or "").strip()
                    if not name or not ips_text:
                        continue
                    for ip in [part.strip() for part in ips_text.split(",")]:
                        if ip:
                            mapping[ip] = name
        except Exception as exc:
            log_exception("device_name_map_load/csv", exc)
            mapping = {}

    # Apply SQLite names only for IPs not present in CSV.
    fallmap = {}
    if app_state_db_path:
        try:
            fallmap = state_store_service.load_fallmap(app_state_db_path)
        except Exception as exc:
            log_exception("device_name_map_load/fallmap_db", exc)
            fallmap = {}
    for ip, name in fallmap.items():
        if ip not in mapping:
            mapping[ip] = name

    with cache_lock:
        cache.clear()
        cache.update(mapping)
        cache_mtime_ns[0] = cache_token
    return mapping
