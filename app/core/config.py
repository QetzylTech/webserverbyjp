"""Runtime configuration helpers for mcweb."""

import os
import secrets


def resolve_secret_key(cfg_get_str, *env_names):
    """Resolve secret key from env/config with secure fallback."""
    for name in env_names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    configured = (cfg_get_str("MCWEB_SECRET_KEY", "") or "").strip()
    if configured:
        return configured
    return secrets.token_hex(32)


def apply_default_flask_config(app):
    """Apply baseline Flask runtime config values."""
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

