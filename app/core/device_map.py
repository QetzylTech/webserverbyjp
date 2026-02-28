"""Device name mapping helpers."""
import csv


def _load_fallmap_text(path):
    """Load fallback device-name entries from text file."""
    mapping = {}
    if path is None:
        return mapping
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return mapping
    for raw in lines:
        line = (raw or "").strip()
        if not line or line.startswith("#"):
            continue
        ip = ""
        name = ""
        if "|" in line:
            ip, name = line.split("|", 1)
        elif "," in line:
            ip, name = line.split(",", 1)
        elif "=" in line:
            ip, name = line.split("=", 1)
        else:
            parts = line.split(None, 1)
            if len(parts) == 2:
                ip, name = parts[0], parts[1]
        ip = (ip or "").strip()
        name = (name or "").strip()
        if ip and name:
            mapping[ip] = name
    return mapping


def get_device_name_map(csv_path, fallback_path, cache_lock, cache, cache_mtime_ns, log_exception):
    """Return cached IP -> device-name map (CSV primary, fallmap.txt fallback)."""
    try:
        current_mtime_ns = csv_path.stat().st_mtime_ns
    except OSError:
        current_mtime_ns = None
    try:
        fallback_mtime_ns = fallback_path.stat().st_mtime_ns if fallback_path else None
    except OSError:
        fallback_mtime_ns = None
    cache_token = (current_mtime_ns, fallback_mtime_ns)
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

    # Apply fallback names only for IPs not present in CSV.
    try:
        fallmap = _load_fallmap_text(fallback_path)
        for ip, name in fallmap.items():
            if ip not in mapping:
                mapping[ip] = name
    except Exception as exc:
        log_exception("device_name_map_load/fallmap", exc)

    with cache_lock:
        cache.clear()
        cache.update(mapping)
        cache_mtime_ns[0] = cache_token
    return mapping
