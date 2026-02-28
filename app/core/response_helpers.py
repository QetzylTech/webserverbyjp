"""Shared Flask response helpers for ajax/non-ajax flows."""

from flask import jsonify, redirect


def is_ajax_request(request):
    """Return True when request expects a JSON/XHR style response."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept.lower()


def ok_response(request):
    """Return default success payload/redirect based on request type."""
    if is_ajax_request(request):
        return jsonify({"ok": True})
    return redirect("/")


def password_rejected_response(request):
    """Return standardized password rejection response."""
    if is_ajax_request(request):
        return jsonify({
            "ok": False,
            "error": "password_incorrect",
            "message": "Password incorrect. Whatever you were trying to do is cancelled.",
        }), 403
    return redirect("/?msg=password_incorrect")


def backup_failed_response(request, message):
    """Return backup failure response with message."""
    if is_ajax_request(request):
        return jsonify({"ok": False, "error": "backup_failed", "message": message}), 500
    return redirect("/?msg=backup_failed")


def start_failed_response(request, message):
    """Return service-start failure response with message."""
    if is_ajax_request(request):
        return jsonify({"ok": False, "error": "start_failed", "message": message}), 500
    return redirect("/?msg=start_failed")


def low_storage_blocked_response(request, message):
    """Return low-storage safety rejection response."""
    if is_ajax_request(request):
        return jsonify({"ok": False, "error": "low_storage_space", "message": message}), 409
    return redirect("/?msg=low_storage_space")


def csrf_rejected_response(request):
    """Return CSRF validation failure response."""
    if is_ajax_request(request):
        return jsonify({
            "ok": False,
            "error": "csrf_invalid",
            "message": "Security check failed. Please refresh and try again.",
        }), 403
    return redirect("/?msg=csrf_invalid")


def rcon_rejected_response(request, message, status_code):
    """Return RCON-specific rejection response."""
    if is_ajax_request(request):
        return jsonify({"ok": False, "message": message}), status_code
    return redirect("/")


def session_write_failed_response(request, debug_note):
    """Return response when session tracking file cannot be updated."""
    message = "Session file write failed."
    if is_ajax_request(request):
        return jsonify({"ok": False, "error": "session_write_failed", "message": f"{message} {debug_note}"}), 500
    return redirect("/?msg=session_write_failed")


def internal_error_response(request):
    """Return generic internal-error response payload/redirect."""
    if is_ajax_request(request):
        return jsonify({"ok": False, "error": "internal_error", "message": "Internal server error."}), 500
    return redirect("/?msg=internal_error")
