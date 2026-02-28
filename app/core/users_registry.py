"""users.txt registry helpers."""
from datetime import datetime


def get_client_ip(request):
    """Resolve best client IP from proxy headers or remote address."""
    forwarded = (request.headers.get("X-Forwarded-For", "") or "").strip()
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return (request.remote_addr or "unknown").strip() or "unknown"


def record_successful_password_ip(
    request,
    users_file,
    users_file_lock,
    display_tz,
    device_name_lookup,
    client_ip=None,
):
        # Track unique validated client IP with latest timestamp and device name.
    ip = (client_ip or get_client_ip(request)).strip() or "unknown"
    timestamp = datetime.now(tz=display_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    device_map = device_name_lookup() or {}
    device_name = (device_map.get(ip, "") or "").strip() or "unmapped-device"
    rows = {}
    try:
        users_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    with users_file_lock:
        try:
            if users_file.exists():
                for raw in users_file.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line:
                        continue
                    if "|" in line:
                        parts = [part.strip() for part in line.split("|")]
                        entry_ip = parts[0] if parts else ""
                        entry_ts = parts[1] if len(parts) >= 2 else ""
                        entry_device = parts[2] if len(parts) >= 3 else "unmapped-device"
                        if entry_ip:
                            rows[entry_ip] = {
                                "timestamp": entry_ts,
                                "device_name": entry_device or "unmapped-device",
                            }
                    else:
                        rows[line] = {"timestamp": "", "device_name": "unmapped-device"}
            rows[ip] = {"timestamp": timestamp, "device_name": device_name}
            content = "\n".join(
                f"{entry_ip}|{rows[entry_ip]['timestamp']}|{rows[entry_ip]['device_name']}"
                for entry_ip in sorted(rows.keys())
            )
            users_file.write_text(f"{content}\n" if content else "", encoding="utf-8")
            return True
        except OSError:
            return False

