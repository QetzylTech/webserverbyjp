"""Maintenance route registration for the MC web dashboard."""

from flask import jsonify, render_template, request

from app.commands import maintenance_commands as maintenance_commands_service
from app.core import profiling
from app.core.rate_limit import InMemoryRateLimiter
from app.queries import maintenance_queries as maintenance_queries_service
from app.routes.shell_page import render_shell_page as render_shell_page_helper

_MAINTENANCE_RATE_LIMITER = InMemoryRateLimiter()


def _maintenance_client_key():
    xff = (request.headers.get("X-Forwarded-For", "") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return str(request.remote_addr or "unknown")


def _maintenance_enforce_rate_limit(route_key, *, limit, window_seconds):
    allowed, retry_after = _MAINTENANCE_RATE_LIMITER.allow(
        f"{route_key}:{_maintenance_client_key()}",
        limit=limit,
        window_seconds=window_seconds,
    )
    if allowed:
        return None
    response = jsonify({
        "ok": False,
        "error": "rate_limited",
        "message": "Too many requests for this endpoint. Please retry shortly.",
        "retry_after_seconds": retry_after,
    })
    response.status_code = 429
    response.headers["Retry-After"] = str(int(retry_after))
    return response


def _json_or_passthrough(result):
    if isinstance(result, tuple):
        return result
    if hasattr(result, 'status_code'):
        return result
    return jsonify(result)


def register_maintenance_routes(app, state):
    """Register maintenance page and maintenance API routes."""
    ctx = getattr(state, "ctx", state)
    maintenance_queries_service.invalidate_state_cache()
    maintenance_commands_service.start_cleanup_scheduler_once(ctx)

    def _maintenance_template_context(scope):
        model = maintenance_queries_service.get_page_model(ctx, state, scope)
        return {
            "csrf_token": state["_ensure_csrf_token"](),
            "maintenance_snapshot": model["snapshot"],
            "maintenance_preview": model["preview"],
            "maintenance_scope": model["scope"],
            "maintenance_device_map": model["device_map"],
            "maintenance_timezone": str(state["DISPLAY_TZ"]),
            "maintenance_active_world": model["active_world"],
            "maintenance_backup_dir": model["backup_dir"],
            "maintenance_stale_dir": model["stale_dir"],
        }

    @app.route("/maintenance")
    def maintenance_page():
        with profiling.timed("maintenance.route.page"):
            scope = maintenance_queries_service.normalize_scope(request.args.get("scope", "backups"))
            context = _maintenance_template_context(scope)
            return render_shell_page_helper(
                app,
                state,
                render_template,
                "fragments/maintenance_fragment.html",
                current_page="maintenance",
                page_title="Cleanup",
                **context,
            )

    @app.route("/maintenance/api/state", methods=["GET"])
    def maintenance_api_state():
        with profiling.timed("maintenance.route.api_state"):
            limited = _maintenance_enforce_rate_limit("maintenance_api_state", limit=30, window_seconds=10.0)
            if limited is not None:
                return limited
            force_refresh = str(request.args.get("refresh", "") or "").strip().lower() in {"1", "true", "yes", "on"}
            scope = maintenance_queries_service.normalize_scope(request.args.get("scope", "backups"))
            return jsonify(maintenance_queries_service.get_state_payload(ctx, state, scope, force_refresh=force_refresh))

    @app.route("/maintenance/api/confirm-password", methods=["POST"])
    def maintenance_api_confirm_password():
        limited = _maintenance_enforce_rate_limit("maintenance_api_confirm_password", limit=15, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        return _json_or_passthrough(maintenance_commands_service.confirm_password(state, payload))

    @app.route("/maintenance/api/save-rules", methods=["POST"])
    def maintenance_api_save_rules():
        limited = _maintenance_enforce_rate_limit("maintenance_api_save_rules", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        result = maintenance_commands_service.save_rules(ctx, state, payload)
        if isinstance(result, dict) and result.get("ok"):
            maintenance_queries_service.invalidate_state_cache(result.get("scope"))
        return _json_or_passthrough(result)

    @app.route("/maintenance/api/run-rules", methods=["POST"])
    def maintenance_api_run_rules():
        limited = _maintenance_enforce_rate_limit("maintenance_api_run_rules", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        result = maintenance_commands_service.run_rules(ctx, state, payload)
        if isinstance(result, dict) and result.get("ok") and not result.get("dry_run"):
            maintenance_queries_service.invalidate_state_cache(result.get("scope"))
        return _json_or_passthrough(result)

    @app.route("/maintenance/api/manual-delete", methods=["POST"])
    def maintenance_api_manual_delete():
        limited = _maintenance_enforce_rate_limit("maintenance_api_manual_delete", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        result = maintenance_commands_service.manual_delete(ctx, state, payload)
        if isinstance(result, dict) and result.get("ok") and not result.get("dry_run"):
            maintenance_queries_service.invalidate_state_cache(result.get("scope"))
        return _json_or_passthrough(result)

    @app.route("/maintenance/api/ack-non-normal", methods=["POST"])
    def maintenance_api_ack_non_normal():
        limited = _maintenance_enforce_rate_limit("maintenance_api_ack_non_normal", limit=10, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        result = maintenance_commands_service.ack_non_normal(ctx, payload)
        if isinstance(result, dict) and result.get("ok"):
            maintenance_queries_service.invalidate_state_cache(result.get("scope"))
        return _json_or_passthrough(result)

