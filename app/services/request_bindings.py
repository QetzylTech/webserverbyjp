"""Build request/response/security delegate callables for main.py."""

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


def build_request_bindings(
    *,
    session_store_service,
    session_state,
    initialize_session_tracking,
    status_debug_note,
    low_storage_error_message,
    users_file,
    users_file_lock,
    display_tz,
    get_device_name_map,
):
    """Return request-scoped callables with explicit runtime dependencies."""

    def _session_write_failed_response():
        return session_write_failed_response(request, status_debug_note())

    def _ensure_csrf_token():
        return ensure_csrf_token(session, lambda: secrets.token_urlsafe(32))

    def _is_csrf_valid():
        return is_csrf_valid(request, session)

    def ensure_session_tracking_initialized():
        session_store_service.ensure_session_tracking_initialized(session_state, initialize_session_tracking)

    def _is_ajax_request():
        return is_ajax_request(request)

    def _ok_response():
        return ok_response(request)

    def _password_rejected_response():
        return password_rejected_response(request)

    def _backup_failed_response(message):
        return backup_failed_response(request, message)

    def _start_failed_response(message):
        return start_failed_response(request, message)

    def _low_storage_blocked_response(message=None):
        msg = message or low_storage_error_message()
        return low_storage_blocked_response(request, msg)

    def _csrf_rejected_response():
        return csrf_rejected_response(request)

    def _rcon_rejected_response(message, status_code):
        return rcon_rejected_response(request, message, status_code)

    def _get_client_ip():
        return get_client_ip(request)

    def record_successful_password_ip(client_ip=None):
        return record_password_ip(
            request=request,
            users_file=users_file,
            users_file_lock=users_file_lock,
            display_tz=display_tz,
            device_name_lookup=get_device_name_map,
            client_ip=client_ip,
        )

    return {
        "_session_write_failed_response": _session_write_failed_response,
        "_ensure_csrf_token": _ensure_csrf_token,
        "_is_csrf_valid": _is_csrf_valid,
        "ensure_session_tracking_initialized": ensure_session_tracking_initialized,
        "_is_ajax_request": _is_ajax_request,
        "_ok_response": _ok_response,
        "_password_rejected_response": _password_rejected_response,
        "_backup_failed_response": _backup_failed_response,
        "_start_failed_response": _start_failed_response,
        "_low_storage_blocked_response": _low_storage_blocked_response,
        "_csrf_rejected_response": _csrf_rejected_response,
        "_rcon_rejected_response": _rcon_rejected_response,
        "_get_client_ip": _get_client_ip,
        "record_successful_password_ip": record_successful_password_ip,
    }
