"""Shared shell/fragment rendering helpers for dashboard routes."""

from flask import request
from markupsafe import Markup


def fragment_response_requested():
    """Return whether the caller requested a fragment-only response."""
    return str(request.headers.get("X-MCWEB-Fragment", "") or "").strip() == "1"


def render_shell_page(app, state, render_template_fn, fragment_template, *, current_page, page_title, **context):
    """Render a live fragment payload or wrap it in the persistent app shell."""
    fragment_html = render_template_fn(fragment_template, current_page=current_page, **context)
    if fragment_response_requested():
        response = app.make_response(fragment_html)
        response.headers["X-MCWEB-Page-Title"] = page_title
        response.headers["X-MCWEB-Page-Key"] = current_page
        return response

    snapshot_getter = None
    try:
        snapshot_getter = state["get_cached_dashboard_metrics"]
    except Exception:
        snapshot_getter = getattr(state, "get_cached_dashboard_metrics", None)
    initial_metrics_snapshot = snapshot_getter() if callable(snapshot_getter) else {}
    cleaned_fragment = fragment_html.strip()
    password_required = True
    csrf_token = ""
    try:
        password_required = bool(state.get("REQUIRE_SUDO_PASSWORD", True))
    except Exception:
        password_required = True
    try:
        csrf_token = str(state["_ensure_csrf_token"]() or "")
    except Exception:
        csrf_token = ""
    return render_template_fn(
        "app_shell.html",
        current_page=current_page,
        page_title=page_title,
        initial_page_html=Markup(cleaned_fragment),
        initial_metrics_snapshot=initial_metrics_snapshot,
        password_required=password_required,
        csrf_token=csrf_token,
    )
