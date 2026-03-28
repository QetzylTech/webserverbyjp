"""Maintenance route registration for the MC web dashboard."""
# mypy: disable-error-code=untyped-decorator

import json
import time
from typing import Any, cast
from flask import Response, jsonify, render_template, request, stream_with_context

from app.commands import maintenance_commands as maintenance_commands_service
from app.core import profiling
from app.core.rate_limit import InMemoryRateLimiter
from app.queries import maintenance_queries as maintenance_queries_service
from app.routes.shell_page import render_shell_page as render_shell_page_helper

_MAINTENANCE_RATE_LIMITER = InMemoryRateLimiter()
_maintenance_commands = cast(Any, maintenance_commands_service)
_maintenance_queries = cast(Any, maintenance_queries_service)


def _maintenance_client_key() -> str:
    xff = (request.headers.get("X-Forwarded-For", "") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return str(request.remote_addr or "unknown")


def _maintenance_enforce_rate_limit(route_key: str, *, limit: int, window_seconds: float) -> Any:
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


def _json_or_passthrough(result: Any) -> Any:
    if isinstance(result, tuple):
        return result
    if hasattr(result, 'status_code'):
        return result
    return jsonify(result)


def register_maintenance_routes(app: Any, state: Any) -> None:
    """Register maintenance page and maintenance API routes."""
    ctx = getattr(state, "ctx", state)
    _maintenance_queries.invalidate_state_cache()
    _maintenance_commands.start_cleanup_scheduler_once(ctx)

    def _maintenance_template_context(scope: str) -> dict[str, object]:
        model = _maintenance_queries.get_page_model(ctx, state, scope)
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
    def maintenance_page() -> Any:
        with profiling.timed("maintenance.route.page"):
            scope = _maintenance_queries.normalize_scope(request.args.get("scope", "backups"))
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
    def maintenance_api_state() -> Any:
        with profiling.timed("maintenance.route.api_state"):
            limited = _maintenance_enforce_rate_limit("maintenance_api_state", limit=30, window_seconds=10.0)
            if limited is not None:
                return limited
            force_refresh = str(request.args.get("refresh", "") or "").strip().lower() in {"1", "true", "yes", "on"}
            scope = _maintenance_queries.normalize_scope(request.args.get("scope", "backups"))
            return jsonify(_maintenance_queries.get_state_payload(ctx, state, scope, force_refresh=force_refresh))

    @app.route("/maintenance-stream")
    def maintenance_stream() -> Response:
        scope = _maintenance_queries.normalize_scope(request.args.get("scope", "backups"))
        force_refresh = str(request.args.get("refresh", "") or "").strip().lower() in {"1", "true", "yes", "on"}

        def generate() -> Any:
            last_payload = ""
            initial_force = force_refresh
            last_keepalive = 0.0
            while True:
                payload = _maintenance_queries.get_state_payload(ctx, state, scope, force_refresh=initial_force)
                initial_force = False
                encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
                if encoded != last_payload:
                    last_payload = encoded
                    yield "event: state\n"
                    yield f"data: {encoded}\n\n"
                    last_keepalive = time.time()
                else:
                    now = time.time()
                    if (now - last_keepalive) >= 1.0:
                        yield ": keepalive\n\n"
                        last_keepalive = now
                time.sleep(1.0)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/maintenance/api/confirm-password", methods=["POST"])
    def maintenance_api_confirm_password() -> Any:
        limited = _maintenance_enforce_rate_limit("maintenance_api_confirm_password", limit=15, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        return _json_or_passthrough(_maintenance_commands.confirm_password(state, payload))

    @app.route("/maintenance/api/save-rules", methods=["POST"])
    def maintenance_api_save_rules() -> Any:
        limited = _maintenance_enforce_rate_limit("maintenance_api_save_rules", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        result = _maintenance_commands.save_rules(ctx, state, payload)
        if isinstance(result, dict) and result.get("ok"):
            _maintenance_queries.invalidate_state_cache(result.get("scope"))
        return _json_or_passthrough(result)

    @app.route("/maintenance/api/run-rules", methods=["POST"])
    def maintenance_api_run_rules() -> Any:
        limited = _maintenance_enforce_rate_limit("maintenance_api_run_rules", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        result = _maintenance_commands.run_rules(ctx, state, payload)
        if isinstance(result, dict) and result.get("ok") and not result.get("dry_run"):
            _maintenance_queries.invalidate_state_cache(result.get("scope"))
        return _json_or_passthrough(result)

    @app.route("/maintenance/api/manual-delete", methods=["POST"])
    def maintenance_api_manual_delete() -> Any:
        limited = _maintenance_enforce_rate_limit("maintenance_api_manual_delete", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        result = _maintenance_commands.manual_delete(ctx, state, payload)
        if isinstance(result, dict) and result.get("ok") and not result.get("dry_run"):
            _maintenance_queries.invalidate_state_cache(result.get("scope"))
        return _json_or_passthrough(result)

    @app.route("/maintenance/api/ack-non-normal", methods=["POST"])
    def maintenance_api_ack_non_normal() -> Any:
        limited = _maintenance_enforce_rate_limit("maintenance_api_ack_non_normal", limit=10, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        result = _maintenance_commands.ack_non_normal(ctx, payload)
        if isinstance(result, dict) and result.get("ok"):
            _maintenance_queries.invalidate_state_cache(result.get("scope"))
        return _json_or_passthrough(result)

