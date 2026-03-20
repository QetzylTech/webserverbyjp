"""Build request-scoped helpers from explicit runtime dependencies."""

import secrets
from pathlib import Path
from typing import Any, Callable

from flask import request, session

from app.core.response_helpers import (
    backup_failed_response,
    csrf_rejected_response,
    is_ajax_request,
    low_storage_blocked_response,
    ok_response,
    password_rejected_response,
    rcon_rejected_response,
    session_write_failed_response,
    start_failed_response,
)
from app.core.security import ensure_csrf_token, is_csrf_valid
from app.core.users_registry import get_client_ip, record_successful_password_ip as record_password_ip

_REQUEST_RESPONSE_HELPERS = {
    "_ok_response": ok_response,
    "_password_rejected_response": password_rejected_response,
    "_backup_failed_response": backup_failed_response,
    "_start_failed_response": start_failed_response,
    "_csrf_rejected_response": csrf_rejected_response,
}
_REQUEST_RESPONSE_HELPERS_MAP: dict[str, Callable[..., Any]] = {
    "_ok_response": ok_response,
    "_password_rejected_response": password_rejected_response,
    "_backup_failed_response": backup_failed_response,
    "_start_failed_response": start_failed_response,
    "_csrf_rejected_response": csrf_rejected_response,
}


def build_request_bindings(
    *,
    session_state: Any,
    initialize_session_tracking: Callable[[], object],
    status_state_note: Callable[[], object],
    low_storage_error_message: Callable[[], object],
    display_tz: Any,
    get_device_name_map: Callable[[], dict[str, str]],
    app_state_db_path: str | Path,
) -> dict[str, Callable[..., Any]]:
    """Return request-aware delegates used by route handlers and lifecycle hooks."""

    def _bind_request(func: Callable[..., Any]) -> Callable[..., Any]:
        def bound(*args: Any, **kwargs: Any) -> Any:
            return func(request, *args, **kwargs)

        return bound

    def _ensure_csrf_token() -> Any:
        return ensure_csrf_token(session, lambda: secrets.token_urlsafe(32))

    def _is_csrf_valid() -> bool:
        return is_csrf_valid(request, session)

    def ensure_session_tracking_initialized() -> None:
        if session_state.initialized:
            return
        with session_state.init_lock:
            if session_state.initialized:
                return
            initialize_session_tracking()
            session_state.initialized = True

    def _session_write_failed_response() -> Any:
        return session_write_failed_response(request, status_state_note())

    def _is_ajax_request() -> bool:
        return is_ajax_request(request)

    def _low_storage_blocked_response(message: object = None) -> Any:
        return low_storage_blocked_response(request, message or low_storage_error_message())

    def _rcon_rejected_response(message: object, status_code: int) -> Any:
        return rcon_rejected_response(request, message, status_code)

    def _get_client_ip() -> str:
        return get_client_ip(request)

    def record_successful_password_ip(client_ip: str | None = None) -> bool:
        return record_password_ip(
            request=request,
            display_tz=display_tz,
            device_name_lookup=get_device_name_map,
            app_state_db_path=app_state_db_path,
            client_ip=client_ip,
        )

    bindings = {
        name: _bind_request(func)
        for name, func in _REQUEST_RESPONSE_HELPERS_MAP.items()
    }
    bindings.update(
        {
            "_session_write_failed_response": _session_write_failed_response,
            "_ensure_csrf_token": _ensure_csrf_token,
            "_is_csrf_valid": _is_csrf_valid,
            "ensure_session_tracking_initialized": ensure_session_tracking_initialized,
            "_is_ajax_request": _is_ajax_request,
            "_low_storage_blocked_response": _low_storage_blocked_response,
            "_rcon_rejected_response": _rcon_rejected_response,
            "_get_client_ip": _get_client_ip,
            "record_successful_password_ip": record_successful_password_ip,
        }
    )
    return bindings
