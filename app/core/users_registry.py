"""User login registry helpers."""
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.core import state_store as state_store_service


def get_client_ip(request: Any) -> str:
    """Resolve best client IP from proxy headers or remote address."""
    forwarded = (request.headers.get("X-Forwarded-For", "") or "").strip()
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return (request.remote_addr or "unknown").strip() or "unknown"


def record_successful_password_ip(
    request: Any,
    display_tz: Any,
    device_name_lookup: Callable[[], dict[str, str] | None],
    app_state_db_path: str | Path,
    client_ip: str | None = None,
) -> bool:
    # Track unique validated client IP with latest timestamp and device name.
    ip = (client_ip or get_client_ip(request)).strip() or "unknown"
    timestamp = datetime.now(tz=display_tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    device_map = device_name_lookup() or {}
    device_name = (device_map.get(ip, "") or "").strip() or "unmapped-device"
    try:
        state_store_service.upsert_user_record(
            app_state_db_path,
            ip=ip,
            timestamp=timestamp,
            device_name=device_name,
        )
        return True
    except Exception:
        return False

