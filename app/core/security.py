"""CSRF/session security helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from typing import Any


def ensure_csrf_token(session: MutableMapping[str, str], token_factory: Callable[[], str]) -> str:
    """Return existing CSRF token from session or create one."""
    token = session.get("csrf_token")
    if not token:
        token = token_factory()
        session["csrf_token"] = token
    return token


def is_csrf_valid(request: Any, session: Mapping[str, str]) -> bool:
    """Validate CSRF token from header or form against session token."""
    expected = session.get("csrf_token")
    if not expected:
        return False
    supplied = (
        request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or ""
    )
    return supplied == expected

