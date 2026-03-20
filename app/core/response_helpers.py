"""Shared Flask response helpers for ajax/non-ajax flows."""
from typing import Any

from flask import jsonify, redirect


def is_ajax_request(request: Any) -> bool:
    """Return True when request expects a JSON/XHR style response."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept.lower()


def ok_response(request: Any) -> Any:
    """Return default success payload/redirect based on request type."""
    if is_ajax_request(request):
        return jsonify({"ok": True})
    return redirect("/")


def password_rejected_response(request: Any) -> Any:
    """Return standardized password rejection response."""
    if is_ajax_request(request):
        return jsonify({
            "ok": False,
            "error": "password_incorrect",
            "message": "Password incorrect. Whatever you were trying to do is cancelled.",
        }), 403
    return redirect("/?msg=password_incorrect")


def backup_failed_response(request: Any, message: object) -> Any:
    """Return backup failure response with message."""
    if is_ajax_request(request):
        return jsonify({"ok": False, "error": "backup_failed", "message": message}), 500
    return redirect("/?msg=backup_failed")


def start_failed_response(request: Any, message: object) -> Any:
    """Return service-start failure response with message."""
    if is_ajax_request(request):
        return jsonify({"ok": False, "error": "start_failed", "message": message}), 500
    return redirect("/?msg=start_failed")


def low_storage_blocked_response(request: Any, message: object) -> Any:
    """Return low-storage safety rejection response."""
    if is_ajax_request(request):
        return jsonify({"ok": False, "error": "low_storage_space", "message": message}), 409
    return redirect("/?msg=low_storage_space")


def csrf_rejected_response(request: Any) -> Any:
    """Return CSRF validation failure response."""
    if is_ajax_request(request):
        return jsonify({
            "ok": False,
            "error": "csrf_invalid",
            "message": "Security check failed. Please refresh and try again.",
        }), 403
    return redirect("/?msg=csrf_invalid")


def rcon_rejected_response(request: Any, message: object, status_code: int) -> Any:
    """Return RCON-specific rejection response."""
    if is_ajax_request(request):
        return jsonify({"ok": False, "message": message}), status_code
    return redirect("/")


def session_write_failed_response(request: Any, debug_note: object) -> Any:
    """Return response when session tracking file cannot be updated."""
    message = "Session file write failed."
    if is_ajax_request(request):
        return jsonify({"ok": False, "error": "session_write_failed", "message": f"{message} {debug_note}"}), 500
    return redirect("/?msg=session_write_failed")


def internal_error_response(request: Any) -> Any:
    """Return generic internal-error response payload/redirect."""
    if is_ajax_request(request):
        return jsonify({"ok": False, "error": "internal_error", "message": "Internal server error."}), 500
    path = str(getattr(request, "path", "") or "").strip()
    msg = str(getattr(request, "args", {}).get("msg", "") or "").strip().lower()
    # Avoid redirect loops for setup/root failures and repeated internal_error redirects.
    if path.startswith("/setup") or path == "/" or msg == "internal_error":
        return ("Internal server error.", 500)
    return redirect("/?msg=internal_error")
