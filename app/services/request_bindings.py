"""Build request-scoped helpers from explicit runtime dependencies."""

import secrets

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


def build_request_bindings(
    *,
    session_store_service,
    session_state,
    initialize_session_tracking,
    status_state_note,
    low_storage_error_message,
    display_tz,
    get_device_name_map,
    app_state_db_path,
):
    """Return request-aware delegates used by route handlers and lifecycle hooks."""

    def _bind_request(func):
        def bound(*args, **kwargs):
            return func(request, *args, **kwargs)

        return bound

    def _ensure_csrf_token():
        return ensure_csrf_token(session, lambda: secrets.token_urlsafe(32))

    def _is_csrf_valid():
        return is_csrf_valid(request, session)

    def ensure_session_tracking_initialized():
        session_store_service.ensure_session_tracking_initialized(session_state, initialize_session_tracking)

    def _session_write_failed_response():
        return session_write_failed_response(request, status_state_note())

    def _is_ajax_request():
        return is_ajax_request(request)

    def _low_storage_blocked_response(message=None):
        return low_storage_blocked_response(request, message or low_storage_error_message())

    def _rcon_rejected_response(message, status_code):
        return rcon_rejected_response(request, message, status_code)

    def _get_client_ip():
        return get_client_ip(request)

    def record_successful_password_ip(client_ip=None):
        return record_password_ip(
            request=request,
            display_tz=display_tz,
            device_name_lookup=get_device_name_map,
            app_state_db_path=app_state_db_path,
            client_ip=client_ip,
        )

    bindings = {
        name: _bind_request(func)
        for name, func in _REQUEST_RESPONSE_HELPERS.items()
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
