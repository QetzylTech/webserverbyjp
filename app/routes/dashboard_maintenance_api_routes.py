"""Maintenance route registration for the MC web dashboard."""
import copy
import threading
import time
from datetime import datetime, timezone

from flask import jsonify, render_template, request
from app.core import profiling
from app.core import state_store as state_store_service
from app.core.rate_limit import InMemoryRateLimiter

from app.services.maintenance_scheduler import start_cleanup_scheduler_once
from app.services.maintenance_state_store import (
    _cleanup_append_history,
    _cleanup_apply_scope_from_state,
    _cleanup_atomic_write_json,
    _cleanup_data_dir,
    _cleanup_error,
    _cleanup_get_client_ip,
    _cleanup_load_config,
    _cleanup_get_scope_view,
    _cleanup_normalize_scope,
    _cleanup_load_non_normal,
    _cleanup_log,
    _cleanup_non_normal_path,
    _cleanup_now_iso,
    _cleanup_save_config,
)
from app.services.maintenance_candidate_scan import _cleanup_active_world_path
from app.services.maintenance_policy import (
    _cleanup_validate_rules,
)
from app.services.maintenance_engine import (
    _cleanup_evaluate,
    _cleanup_run_with_lock,
    _cleanup_state_snapshot,
)
from app.services.worker_scheduler import WorkerSpec, start_worker

_MAINTENANCE_STATE_CACHE_TTL_SECONDS = 3.0
_MAINTENANCE_STATE_CACHE_LOCK = threading.Lock()
_MAINTENANCE_STATE_CACHE = {}
_MAINTENANCE_RATE_LIMITER = InMemoryRateLimiter()
_MAINTENANCE_ASYNC_REFRESH_INTERVAL_SECONDS = 2.0
_MAINTENANCE_ASYNC_SCOPE_IDLE_SECONDS = 45.0
_MAINTENANCE_ASYNC_LOCK = threading.Lock()
_MAINTENANCE_ASYNC_STATE = {
    "started": False,
    "state_ref": None,
    "ctx_ref": None,
    "scope_items": {},
}


def _maintenance_state_cache_get(scope):
    """Return cached maintenance API payload for scope when still fresh."""
    now = time.time()
    with _MAINTENANCE_STATE_CACHE_LOCK:
        item = _MAINTENANCE_STATE_CACHE.get(scope)
        if not isinstance(item, dict):
            return None
        if float(item.get("expires_at", 0.0)) < now:
            _MAINTENANCE_STATE_CACHE.pop(scope, None)
            return None
        payload = item.get("payload")
        return copy.deepcopy(payload) if isinstance(payload, dict) else None


def _maintenance_state_cache_set(scope, payload):
    """Store maintenance API payload cache entry for scope."""
    if not isinstance(payload, dict):
        return
    with _MAINTENANCE_STATE_CACHE_LOCK:
        _MAINTENANCE_STATE_CACHE[scope] = {
            "expires_at": time.time() + _MAINTENANCE_STATE_CACHE_TTL_SECONDS,
            "payload": copy.deepcopy(payload),
        }


def _maintenance_state_cache_invalidate(scope=None):
    """Invalidate maintenance API payload cache (single scope or all)."""
    with _MAINTENANCE_STATE_CACHE_LOCK:
        if scope is None:
            _MAINTENANCE_STATE_CACHE.clear()
        else:
            _MAINTENANCE_STATE_CACHE.pop(scope, None)
    with _MAINTENANCE_ASYNC_LOCK:
        if scope is None:
            _MAINTENANCE_ASYNC_STATE["scope_items"].clear()
        else:
            _MAINTENANCE_ASYNC_STATE["scope_items"].pop(scope, None)


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


def _maintenance_compute_state_payload(ctx, state, scope):
    full_cfg = _cleanup_load_config(ctx)
    cfg = _cleanup_get_scope_view(full_cfg, scope)
    preview = _cleanup_evaluate(ctx, cfg, mode="rule", apply_changes=False, trigger="preview")
    return {
        "ok": True,
        **_cleanup_state_snapshot(ctx, cfg),
        "preview": preview,
        "scope": scope,
        "device_map": state["get_device_name_map"](),
    }


def _maintenance_payload_from_db(state, scope):
    """Return worker-precomputed maintenance payload from sqlite events when available."""
    db_path = state.get("APP_STATE_DB_PATH")
    if db_path is None:
        return None, 0.0
    try:
        event = state_store_service.get_latest_event(db_path, topic=f"maintenance_state:{scope}")
    except Exception:
        return None, 0.0
    if not isinstance(event, dict):
        return None, 0.0
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None, 0.0
    snapshot = payload.get("snapshot")
    preview = payload.get("preview")
    if not isinstance(snapshot, dict) or not isinstance(preview, dict):
        return None, 0.0
    data = {
        "ok": True,
        **snapshot,
        "preview": preview,
        "scope": scope,
        "device_map": state["get_device_name_map"](),
    }
    try:
        computed_at = float(event.get("id", 0) or 0)
    except Exception:
        computed_at = 0.0
    return data, computed_at


def _maintenance_payload_with_freshness(payload, *, computed_at, refreshing=False):
    body = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    computed_epoch = float(computed_at or 0.0)
    stale_seconds = max(0.0, time.time() - computed_epoch) if computed_epoch > 0 else 0.0
    body["freshness"] = {
        "computed_at_epoch": computed_epoch,
        "computed_at_iso": datetime.fromtimestamp(computed_epoch, tz=timezone.utc).isoformat() if computed_epoch > 0 else "",
        "stale_seconds": stale_seconds,
        "refreshing": bool(refreshing),
    }
    return body


def _maintenance_async_worker():
    while True:
        time.sleep(_MAINTENANCE_ASYNC_REFRESH_INTERVAL_SECONDS)
        with _MAINTENANCE_ASYNC_LOCK:
            state = _MAINTENANCE_ASYNC_STATE.get("state_ref")
            ctx = _MAINTENANCE_ASYNC_STATE.get("ctx_ref")
            scope_items = _MAINTENANCE_ASYNC_STATE.get("scope_items", {})
            scope_rows = [(k, dict(v)) for k, v in scope_items.items() if isinstance(v, dict)]
        if state is None or ctx is None:
            continue
        now = time.time()
        for scope, item in scope_rows:
            last_requested_at = float(item.get("last_requested_at", 0.0) or 0.0)
            if (now - last_requested_at) > _MAINTENANCE_ASYNC_SCOPE_IDLE_SECONDS:
                continue
            computed_at = float(item.get("computed_at", 0.0) or 0.0)
            force_refresh = bool(item.get("force_refresh", False))
            stale = (now - computed_at) > _MAINTENANCE_STATE_CACHE_TTL_SECONDS
            if not (force_refresh or stale):
                continue
            with _MAINTENANCE_ASYNC_LOCK:
                current = _MAINTENANCE_ASYNC_STATE["scope_items"].setdefault(scope, {})
                if bool(current.get("refreshing", False)):
                    continue
                current["refreshing"] = True
                if force_refresh:
                    current["force_refresh"] = False
            try:
                payload = _maintenance_compute_state_payload(ctx, state, scope)
                computed_now = time.time()
                with _MAINTENANCE_ASYNC_LOCK:
                    target = _MAINTENANCE_ASYNC_STATE["scope_items"].setdefault(scope, {})
                    target["payload"] = payload
                    target["computed_at"] = computed_now
                    target["refreshing"] = False
                _maintenance_state_cache_set(scope, _maintenance_payload_with_freshness(payload, computed_at=computed_now, refreshing=False))
            except Exception:
                with _MAINTENANCE_ASYNC_LOCK:
                    target = _MAINTENANCE_ASYNC_STATE["scope_items"].setdefault(scope, {})
                    target["refreshing"] = False


def _maintenance_mark_scope_requested(ctx, state, scope, *, force_refresh=False):
    with _MAINTENANCE_ASYNC_LOCK:
        _MAINTENANCE_ASYNC_STATE["state_ref"] = state
        _MAINTENANCE_ASYNC_STATE["ctx_ref"] = ctx
        item = _MAINTENANCE_ASYNC_STATE["scope_items"].setdefault(scope, {})
        item["last_requested_at"] = time.time()
        if force_refresh:
            item["force_refresh"] = True
        if not _MAINTENANCE_ASYNC_STATE["started"]:
            start_worker(
                ctx,
                WorkerSpec(
                    name="maintenance-async-worker",
                    target=_maintenance_async_worker,
                    interval_source=1.0,
                    stop_signal_name="maintenance_async_worker_stop_event",
                    health_marker="maintenance_async_worker",
                ),
            )
            _MAINTENANCE_ASYNC_STATE["started"] = True


def _maintenance_get_async_item(scope):
    with _MAINTENANCE_ASYNC_LOCK:
        item = _MAINTENANCE_ASYNC_STATE["scope_items"].get(scope)
        return dict(item) if isinstance(item, dict) else None


def _maintenance_set_async_item(scope, payload, *, computed_at=None):
    with _MAINTENANCE_ASYNC_LOCK:
        item = _MAINTENANCE_ASYNC_STATE["scope_items"].setdefault(scope, {})
        item["payload"] = copy.deepcopy(payload) if isinstance(payload, dict) else {}
        item["computed_at"] = float(computed_at or time.time())
        item["refreshing"] = False


def register_maintenance_routes(app, state):
    """Register maintenance page and maintenance API routes."""
    ctx = getattr(state, "ctx", state)
    _maintenance_state_cache_invalidate()
    start_cleanup_scheduler_once(ctx)

    def _require_password(payload, *, what, why, trigger, scope, details="", log_success=False):
        sudo_password = str(payload.get("sudo_password", ""))
        if state["validate_sudo_password"](sudo_password):
            state["record_successful_password_ip"]()
            if log_success:
                _cleanup_log(
                    state,
                    what=what,
                    why=why,
                    trigger=trigger,
                    result="ok",
                    details=f"scope={scope};{details}".strip(";"),
                )
            return True, None
        _cleanup_log(
            state,
            what=what,
            why=why,
            trigger=trigger,
            result="invalid_password",
            details=f"scope={scope};{details}".strip(";"),
        )
        return False, _cleanup_error("invalid_password", status=403)

    # Route: /maintenance
    @app.route("/maintenance")
    def maintenance_page():
        """Runtime helper maintenance_page."""
        with profiling.timed("maintenance.route.page"):
            full_cfg = _cleanup_load_config(ctx)
            scope = _cleanup_normalize_scope(request.args.get("scope", "backups"))
            cfg = _cleanup_get_scope_view(full_cfg, scope)
            snapshot = _cleanup_state_snapshot(ctx, cfg)
            eval_preview = _cleanup_evaluate(ctx, cfg, mode="rule", apply_changes=False, trigger="preview")
            return render_template(
                "maintenance.html",
                current_page="maintenance",
                csrf_token=state["_ensure_csrf_token"](),
                maintenance_snapshot=snapshot,
                maintenance_preview=eval_preview,
                maintenance_scope=scope,
                maintenance_device_map=state["get_device_name_map"](),
                maintenance_timezone=str(state["DISPLAY_TZ"]),
                maintenance_active_world=str(_cleanup_active_world_path(ctx) or state["WORLD_DIR"]),
                maintenance_backup_dir=str(state["BACKUP_DIR"]),
                maintenance_stale_dir=str((_cleanup_data_dir(ctx) / "old_worlds").resolve()),
            )

    # Route: /maintenance/api/state
    @app.route("/maintenance/api/state", methods=["GET"])
    def maintenance_api_state():
        """Handle maintenance api state."""
        with profiling.timed("maintenance.route.api_state"):
            limited = _maintenance_enforce_rate_limit("maintenance_api_state", limit=30, window_seconds=10.0)
            if limited is not None:
                return limited
            force_refresh = str(request.args.get("refresh", "")).strip().lower() in {"1", "true", "yes", "on"}
            scope = _cleanup_normalize_scope(request.args.get("scope", "backups"))
            _maintenance_mark_scope_requested(ctx, state, scope, force_refresh=force_refresh)
            if not force_refresh:
                db_payload, _db_id = _maintenance_payload_from_db(state, scope)
                if isinstance(db_payload, dict):
                    response_payload = _maintenance_payload_with_freshness(
                        db_payload,
                        computed_at=time.time(),
                        refreshing=False,
                    )
                    _maintenance_state_cache_set(scope, response_payload)
                    return jsonify(response_payload)
            if not force_refresh:
                cached = _maintenance_state_cache_get(scope)
                if isinstance(cached, dict):
                    return jsonify(cached)
            async_item = _maintenance_get_async_item(scope)
            if isinstance(async_item, dict) and isinstance(async_item.get("payload"), dict):
                computed_at = float(async_item.get("computed_at", 0.0) or 0.0)
                refreshing = bool(async_item.get("refreshing", False))
                payload = _maintenance_payload_with_freshness(
                    async_item.get("payload", {}),
                    computed_at=computed_at,
                    refreshing=refreshing or force_refresh,
                )
                _maintenance_state_cache_set(scope, payload)
                return jsonify(payload)
            payload = _maintenance_compute_state_payload(ctx, state, scope)
            computed_at = time.time()
            _maintenance_set_async_item(scope, payload, computed_at=computed_at)
            response_payload = _maintenance_payload_with_freshness(payload, computed_at=computed_at, refreshing=False)
            _maintenance_state_cache_set(scope, response_payload)
            return jsonify(response_payload)

    # Route: /maintenance/api/confirm-password
    @app.route("/maintenance/api/confirm-password", methods=["POST"])
    def maintenance_api_confirm_password():
        """Validate maintenance password for protected UI actions."""
        limited = _maintenance_enforce_rate_limit("maintenance_api_confirm_password", limit=15, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        scope = _cleanup_normalize_scope(payload.get("scope", "backups"))
        action = str(payload.get("action", "")).strip().lower()
        action_map = {
            "open_rules_edit": ("confirm_password", "open_rules_edit", "manual"),
            "save_rules": ("confirm_password", "save_rules", "manual"),
            "run_rules": ("confirm_password", "run_rules", "manual"),
            "manual_delete": ("confirm_password", "manual_delete", "manual"),
        }
        if action not in action_map:
            return _cleanup_error("validation_failure", "Unsupported action.", status=400)
        what, why, trigger = action_map[action]
        ok, err = _require_password(
            payload,
            what=what,
            why=why,
            trigger=trigger,
            scope=scope,
            details=f"action={action}",
            log_success=True,
        )
        if not ok:
            return err
        return jsonify({"ok": True, "scope": scope, "action": action})

    # Route: /maintenance/api/save-rules
    @app.route("/maintenance/api/save-rules", methods=["POST"])
    def maintenance_api_save_rules():
        """Handle maintenance api save rules."""
        limited = _maintenance_enforce_rate_limit("maintenance_api_save_rules", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        scope = _cleanup_normalize_scope(payload.get("scope", "backups"))
        ok_pw, err = _require_password(payload, what="save_rules", why="manual_save", trigger="manual", scope=scope)
        if not ok_pw:
            return err
        ok, parsed = _cleanup_validate_rules(payload.get("rules", {}))
        if not ok:
            _cleanup_log(ctx, what="save_rules", why="manual_save", trigger="manual", result="validation_failure", details=f"scope={scope};error={parsed}")
            return _cleanup_error("validation_failure", parsed, status=400)
        full_cfg = _cleanup_load_config(ctx)
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        cfg["rules"] = _cleanup_apply_scope_from_state(ctx, parsed, scope=scope)
        time_based = cfg.get("rules", {}).get("time_based", {})
        time_enabled = bool(time_based.get("enabled", True))
        repeat_mode = str(time_based.get("repeat_mode", "does_not_repeat")).strip().lower()
        if not time_enabled or repeat_mode == "does_not_repeat":
            cfg["schedules"] = []
        else:
            interval_map = {
                "daily": "daily",
                "weekly": "weekly",
                "monthly": "monthly",
                "weekdays": "weekdays",
                "every_n_days": "every_n_days",
            }
            weekly_day_map = {
                "Sunday": 6,
                "Monday": 0,
                "Tuesday": 1,
                "Wednesday": 2,
                "Thursday": 3,
                "Friday": 4,
                "Saturday": 5,
            }
            cfg["schedules"] = [
                {
                    "id": "time-based-rule",
                    "mode": "time",
                    "enabled": True,
                    "interval": interval_map.get(repeat_mode, "daily"),
                    "time": str(time_based.get("time_of_backup", "03:00")),
                    "day_of_week": int(weekly_day_map.get(str(time_based.get("weekly_day", "Sunday")), 6)),
                    "day_of_month": int(time_based.get("monthly_date", 1)),
                    "every_n_days": int(time_based.get("every_n_days", 1)),
                    "anchor_date": _cleanup_now_iso(ctx)[:10],
                }
            ]
        meta = cfg.setdefault("meta", {})
        meta["rule_version"] = int(meta.get("rule_version", 0)) + 1
        meta["schedule_version"] = int(meta.get("schedule_version", 0)) + 1
        meta["last_changed_by"] = _cleanup_get_client_ip(ctx)
        meta["last_changed_at"] = _cleanup_now_iso(ctx)
        _cleanup_save_config(ctx, full_cfg)
        _maintenance_state_cache_invalidate(scope)
        _cleanup_log(
            state,
            what="save_rules",
            why="manual_save",
            trigger="manual",
            result="ok",
            details=f"scope={scope};rule_version={meta['rule_version']}",
        )
        preview = _cleanup_evaluate(ctx, cfg, mode="rule", apply_changes=False, trigger="preview")
        return jsonify({"ok": True, "config": cfg, "preview": preview, "scope": scope})

    # Route: /maintenance/api/run-rules
    @app.route("/maintenance/api/run-rules", methods=["POST"])
    def maintenance_api_run_rules():
        """Handle maintenance api run rules."""
        limited = _maintenance_enforce_rate_limit("maintenance_api_run_rules", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        scope = _cleanup_normalize_scope(payload.get("scope", "backups"))
        selected_rule = str(payload.get("rule_key", "")).strip().lower()
        if selected_rule not in {"", "age", "count", "space"}:
            return _cleanup_error("validation_failure", "rule_key must be one of: age, count, space.", status=400)
        raw_dry_run = payload.get("dry_run", False)
        dry_run = bool(raw_dry_run)
        if isinstance(raw_dry_run, str):
            dry_run = raw_dry_run.strip().lower() in {"1", "true", "yes", "on"}
        full_cfg = _cleanup_load_config(ctx)
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        if not cfg.get("rules", {}).get("enabled", True):
            _cleanup_log(ctx, what="run_rules", why="manual_apply", trigger="manual_rule", result="rules_disabled", details=f"scope={scope}")
            return _cleanup_error("rules_disabled", status=400)
        eval_cfg = cfg
        if selected_rule:
            eval_cfg = copy.deepcopy(cfg)
            rules = eval_cfg.setdefault("rules", {})
            rules["enabled"] = True
            for key in ("age", "count", "space"):
                sub = rules.setdefault(key, {})
                sub["enabled"] = key == selected_rule
        if dry_run:
            preview = _cleanup_evaluate(ctx, eval_cfg, mode="rule", apply_changes=False, trigger="manual_rule")
            _cleanup_append_history(
                ctx,
                trigger=f"manual_rule:{selected_rule or 'all'}",
                mode="rule",
                dry_run=True,
                deleted_count=0,
                errors_count=0,
                requested_count=preview.get("requested_delete_count", 0),
                capped_count=preview.get("capped_delete_count", 0),
                result="dry_run",
                scope=scope,
            )
            _cleanup_log(
                ctx,
                what="run_rules",
                why="manual_apply_dry_run",
                trigger="manual_rule",
                result="dry_run",
                details=f"scope={scope};rule={selected_rule or 'all'};requested={preview['requested_delete_count']};capped={preview['capped_delete_count']}",
            )
            return jsonify({"ok": True, "dry_run": True, "preview": preview, "config": cfg, "scope": scope})
        ok_pw, err = _require_password(payload, what="run_rules", why="manual_apply", trigger="manual", scope=scope)
        if not ok_pw:
            return err
        result = _cleanup_run_with_lock(ctx, eval_cfg, mode="rule", trigger="manual_rule")
        if result is None:
            _cleanup_log(ctx, what="run_rules", why="manual_apply", trigger="manual_rule", result="lock_held", details=f"scope={scope}")
            return _cleanup_error("lock_held", status=409)
        meta = cfg.setdefault("meta", {})
        meta["last_run_at"] = _cleanup_now_iso(ctx)
        meta["last_run_trigger"] = "manual_rule"
        meta["last_run_result"] = "ok" if not result["errors"] else "partial"
        meta["last_run_deleted"] = result["deleted_count"]
        meta["last_run_errors"] = len(result["errors"])
        _cleanup_save_config(ctx, full_cfg)
        _maintenance_state_cache_invalidate(scope)
        _cleanup_append_history(
            ctx,
            trigger=f"manual_rule:{selected_rule or 'all'}",
            mode="rule",
            dry_run=False,
            deleted_count=result["deleted_count"],
            errors_count=len(result["errors"]),
            requested_count=result.get("requested_delete_count", 0),
            capped_count=result.get("capped_delete_count", result["deleted_count"]),
            result=meta["last_run_result"],
            scope=scope,
        )
        _cleanup_log(
            ctx,
            what="run_rules",
            why="manual_apply",
            trigger="manual_rule",
            result=meta["last_run_result"],
            details=f"scope={scope};rule={selected_rule or 'all'};deleted={result['deleted_count']};errors={len(result['errors'])}",
        )
        return jsonify({"ok": True, "result": result, "config": cfg, "scope": scope})

    # Route: /maintenance/api/manual-delete
    @app.route("/maintenance/api/manual-delete", methods=["POST"])
    def maintenance_api_manual_delete():
        """Handle maintenance api manual delete."""
        limited = _maintenance_enforce_rate_limit("maintenance_api_manual_delete", limit=8, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        scope = _cleanup_normalize_scope(payload.get("scope", "backups"))
        raw_dry_run = payload.get("dry_run", False)
        dry_run = bool(raw_dry_run)
        if isinstance(raw_dry_run, str):
            dry_run = raw_dry_run.strip().lower() in {"1", "true", "yes", "on"}
        selected = payload.get("selected_paths", [])
        if not isinstance(selected, list):
            return _cleanup_error("validation_failure", "selected_paths must be a list.", status=400)
        full_cfg = _cleanup_load_config(ctx)
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        preview = _cleanup_evaluate(ctx, cfg, mode="manual", selected_paths=selected, apply_changes=False, trigger="manual_selection")
        if preview["selected_ineligible"]:
            _cleanup_log(
                state,
                what="manual_delete",
                why="manual_selection",
                trigger="manual",
                result="ineligible_selection",
                details=f"count={len(preview['selected_ineligible'])}",
            )
            return _cleanup_error("ineligible_selection", {"paths": preview["selected_ineligible"]}, status=409)
        if dry_run:
            _cleanup_append_history(
                ctx,
                trigger="manual_selection",
                mode="manual",
                dry_run=True,
                deleted_count=0,
                errors_count=0,
                requested_count=preview.get("requested_delete_count", 0),
                capped_count=preview.get("capped_delete_count", 0),
                result="dry_run",
                scope=scope,
            )
            _cleanup_log(
                ctx,
                what="manual_delete",
                why="manual_selection_dry_run",
                trigger="manual_selection",
                result="dry_run",
                details=f"scope={scope};selected={len(selected)};capped={preview['capped_delete_count']}",
            )
            return jsonify({"ok": True, "dry_run": True, "preview": preview, "config": cfg, "scope": scope})
        ok_pw, err = _require_password(payload, what="manual_delete", why="manual_selection", trigger="manual", scope=scope)
        if not ok_pw:
            return err
        result = _cleanup_run_with_lock(ctx, cfg, mode="manual", selected_paths=selected, trigger="manual_selection")
        if result is None:
            _cleanup_log(ctx, what="manual_delete", why="manual_selection", trigger="manual_selection", result="lock_held", details=f"scope={scope}")
            return _cleanup_error("lock_held", status=409)
        meta = cfg.setdefault("meta", {})
        meta["last_run_at"] = _cleanup_now_iso(ctx)
        meta["last_run_trigger"] = "manual_selection"
        meta["last_run_result"] = "ok" if not result["errors"] else "partial"
        meta["last_run_deleted"] = result["deleted_count"]
        meta["last_run_errors"] = len(result["errors"])
        _cleanup_save_config(ctx, full_cfg)
        _maintenance_state_cache_invalidate(scope)
        _cleanup_append_history(
            ctx,
            trigger="manual_selection",
            mode="manual",
            dry_run=False,
            deleted_count=result["deleted_count"],
            errors_count=len(result["errors"]),
            requested_count=result.get("requested_delete_count", 0),
            capped_count=result.get("capped_delete_count", result["deleted_count"]),
            result=meta["last_run_result"],
            scope=scope,
        )
        _cleanup_log(
            ctx,
            what="manual_delete",
            why="manual_selection",
            trigger="manual_selection",
            result=meta["last_run_result"],
            details=f"scope={scope};deleted={result['deleted_count']};errors={len(result['errors'])}",
        )
        return jsonify({"ok": True, "result": result, "config": cfg, "scope": scope})

    # Route: /maintenance/api/ack-non-normal
    @app.route("/maintenance/api/ack-non-normal", methods=["POST"])
    def maintenance_api_ack_non_normal():
        """Handle maintenance api ack non normal."""
        limited = _maintenance_enforce_rate_limit("maintenance_api_ack_non_normal", limit=10, window_seconds=30.0)
        if limited is not None:
            return limited
        payload = request.get_json(silent=True) or {}
        scope = _cleanup_normalize_scope(payload.get("scope", "backups"))

        def _entry_scope(entry):
            if not isinstance(entry, dict):
                return ""
            raw_scope = str(entry.get("scope", "")).strip().lower()
            if raw_scope in {"backups", "stale_worlds"}:
                return raw_scope
            schedule_id = str(entry.get("schedule_id", "")).strip().lower()
            if schedule_id.startswith("backups:"):
                return "backups"
            if schedule_id.startswith("stale_worlds:"):
                return "stale_worlds"
            return ""

        data = _cleanup_load_non_normal(ctx)
        missed = data.get("missed_runs")
        if not isinstance(missed, list):
            missed = []
        # Remove current-scope events and unknown-scope events.
        data["missed_runs"] = [item for item in missed if (_entry_scope(item) not in {"", scope})]
        data["last_ack_at"] = _cleanup_now_iso(ctx)
        data["last_ack_by"] = _cleanup_get_client_ip(ctx)
        _cleanup_atomic_write_json(_cleanup_non_normal_path(ctx), data)
        _maintenance_state_cache_invalidate(scope)
        _cleanup_log(ctx, what="ack_non_normal", why="manual_ack", trigger="manual", result="ok", details=f"scope={scope}")
        return jsonify({"ok": True, "non_normal": data})

