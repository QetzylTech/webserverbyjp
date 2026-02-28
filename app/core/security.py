"""CSRF/session security helpers."""


def ensure_csrf_token(session, token_factory):
    """Return existing CSRF token from session or create one."""
    token = session.get("csrf_token")
    if not token:
        token = token_factory()
        session["csrf_token"] = token
    return token


def is_csrf_valid(request, session):
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

