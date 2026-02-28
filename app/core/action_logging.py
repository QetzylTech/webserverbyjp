"""Action/error logging helpers with request-aware client identification."""

from datetime import datetime
import traceback
from flask import request, has_request_context


def sanitize_log_fragment(text):
    """Normalize user/system text into a single safe log line fragment."""
    return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split()).strip()


def get_client_ip():
    """Resolve the best client IP from proxy headers or direct connection."""
    if not has_request_context():
        return "mcweb"
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    x_real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if x_real_ip:
        return x_real_ip
    direct = (request.remote_addr or "").strip()
    return direct or "mcweb"


def make_log_action(display_tz, log_dir, action_log_file):
    """Build and return the structured action logger closure."""

    def log_action(action, command=None, rejection_message=None):
        """Append one action event line; failures are intentionally swallowed."""
        timestamp = datetime.now(tz=display_tz).strftime("%b %d %H:%M:%S")
        client_ip = sanitize_log_fragment(get_client_ip()) or "unknown"
        safe_action = sanitize_log_fragment(action) or "unknown"
        parts = [f"{timestamp} <{client_ip}> [mcweb/{safe_action}]"]
        if command:
            safe_command = sanitize_log_fragment(command)
            if safe_command:
                parts.append(safe_command)
        if rejection_message:
            safe_rejection = sanitize_log_fragment(rejection_message)
            if safe_rejection:
                parts.append(f"rejected: {safe_rejection}")
        line = " ".join(parts).strip()
        if not line:
            return
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with action_log_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            # Logging must not break control endpoints.
            pass

    return log_action


def make_log_exception(log_action):
    """Build and return an exception logger that emits through log_action."""

    def log_exception(context, exc):
        """Log a compact exception summary with a truncated traceback."""
        exc_name = type(exc).__name__ if exc is not None else "Exception"
        exc_text = sanitize_log_fragment(str(exc) if exc is not None else "")
        tb = ""
        if exc is not None:
            tb = sanitize_log_fragment(" | ".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        message = f"{context}: {exc_name}"
        if exc_text:
            message += f": {exc_text}"
        if tb:
            message += f" | traceback: {tb[:700]}"
        log_action("error", rejection_message=message)

    return log_exception
