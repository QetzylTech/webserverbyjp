"""Runtime configuration helpers for mcweb."""

from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any


def resolve_secret_key(cfg_get_str: Callable[[str, str], str], *env_names: str) -> str:
    """Resolve secret key from env/config."""
    for name in env_names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    configured = (cfg_get_str("MCWEB_SECRET_KEY", "") or "").strip()
    if configured:
        return configured
    raise RuntimeError("Missing required MCWEB_SECRET_KEY configuration.")


def apply_default_flask_config(app: Any) -> None:
    """Apply baseline Flask runtime config values."""
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

