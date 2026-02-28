"""Maintenance route registration for the MC web dashboard."""
import copy

from flask import jsonify, render_template, request

from app.services.maintenance_scheduler import start_cleanup_scheduler_once
from app.services.maintenance_basics import (
    _cleanup_append_history,
    _cleanup_apply_scope_from_state,
    _cleanup_atomic_write_json,
    _cleanup_data_dir,
    _cleanup_error,
    _cleanup_get_client_ip,
    _cleanup_json_path,
    _cleanup_load_config,
    _cleanup_get_scope_view,
    _cleanup_normalize_scope,
    _cleanup_load_non_normal,
    _cleanup_log,
    _cleanup_non_normal_path,
    _cleanup_now_iso,
)
from app.services.maintenance_candidates import _cleanup_active_world_path
from app.services.maintenance_rules import (
    _cleanup_validate_rules,
    _cleanup_validate_schedules,
)
from app.services.maintenance_runtime import (
    _cleanup_evaluate,
    _cleanup_run_with_lock,
    _cleanup_state_snapshot,
)

def register_maintenance_routes(app, state):
    """Register maintenance page and maintenance API routes."""
    start_cleanup_scheduler_once(state)

    # Route: /maintenance
    @app.route("/maintenance")
    def maintenance_page():
        """Runtime helper maintenance_page."""
        full_cfg = _cleanup_load_config(state)
        scope = _cleanup_normalize_scope(request.args.get("scope", "backups"))
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        snapshot = _cleanup_state_snapshot(state, cfg)
        eval_preview = _cleanup_evaluate(state, cfg, mode="rule", apply_changes=False, trigger="preview")
        return render_template(
            "maintenance.html",
            current_page="maintenance",
            csrf_token=state["_ensure_csrf_token"](),
            maintenance_snapshot=snapshot,
            maintenance_preview=eval_preview,
            maintenance_scope=scope,
            maintenance_timezone=str(state["DISPLAY_TZ"]),
            maintenance_active_world=str(_cleanup_active_world_path(state) or state["WORLD_DIR"]),
            maintenance_backup_dir=str(state["BACKUP_DIR"]),
            maintenance_stale_dir=str((_cleanup_data_dir(state) / "old_worlds").resolve()),
        )

    # Route: /maintenance/api/state
    @app.route("/maintenance/api/state", methods=["GET"])
    def maintenance_api_state():
        """Handle maintenance api state."""
        full_cfg = _cleanup_load_config(state)
        scope = _cleanup_normalize_scope(request.args.get("scope", "backups"))
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        preview = _cleanup_evaluate(state, cfg, mode="rule", apply_changes=False, trigger="preview")
        return jsonify({"ok": True, **_cleanup_state_snapshot(state, cfg), "preview": preview, "scope": scope})

    # Route: /maintenance/api/save-rules
    @app.route("/maintenance/api/save-rules", methods=["POST"])
    def maintenance_api_save_rules():
        """Handle maintenance api save rules."""
        payload = request.get_json(silent=True) or {}
        scope = _cleanup_normalize_scope(payload.get("scope", "backups"))
        ok, parsed = _cleanup_validate_rules(payload.get("rules", {}))
        if not ok:
            return _cleanup_error("validation_failure", parsed, status=400)
        full_cfg = _cleanup_load_config(state)
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        cfg["rules"] = _cleanup_apply_scope_from_state(state, parsed, scope=scope)
        time_based = cfg.get("rules", {}).get("time_based", {})
        repeat_mode = str(time_based.get("repeat_mode", "does_not_repeat")).strip().lower()
        if repeat_mode == "does_not_repeat":
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
                    "anchor_date": _cleanup_now_iso(state)[:10],
                }
            ]
        meta = cfg.setdefault("meta", {})
        meta["rule_version"] = int(meta.get("rule_version", 0)) + 1
        meta["schedule_version"] = int(meta.get("schedule_version", 0)) + 1
        meta["last_changed_by"] = _cleanup_get_client_ip(state)
        meta["last_changed_at"] = _cleanup_now_iso(state)
        _cleanup_atomic_write_json(_cleanup_json_path(state), full_cfg)
        _cleanup_log(
            state,
            what="save_rules",
            why="manual_save",
            trigger="manual",
            result="ok",
            details=f"scope={scope};rule_version={meta['rule_version']}",
        )
        preview = _cleanup_evaluate(state, cfg, mode="rule", apply_changes=False, trigger="preview")
        return jsonify({"ok": True, "config": cfg, "preview": preview, "scope": scope})

    # Route: /maintenance/api/save-schedules
    @app.route("/maintenance/api/save-schedules", methods=["POST"])
    def maintenance_api_save_schedules():
        """Handle maintenance api save schedules."""
        payload = request.get_json(silent=True) or {}
        scope = _cleanup_normalize_scope(payload.get("scope", "backups"))
        ok, parsed = _cleanup_validate_schedules(payload.get("schedules", []))
        if not ok:
            code = "schedule_conflict" if "conflict" in str(parsed).lower() else "validation_failure"
            return _cleanup_error(code, parsed, status=400)
        full_cfg = _cleanup_load_config(state)
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        cfg["schedules"] = parsed
        meta = cfg.setdefault("meta", {})
        meta["schedule_version"] = int(meta.get("schedule_version", 0)) + 1
        meta["last_changed_by"] = _cleanup_get_client_ip(state)
        meta["last_changed_at"] = _cleanup_now_iso(state)
        _cleanup_atomic_write_json(_cleanup_json_path(state), full_cfg)
        _cleanup_log(
            state,
            what="save_schedules",
            why="manual_save",
            trigger="manual",
            result="ok",
            details=f"scope={scope};schedule_version={meta['schedule_version']}",
        )
        return jsonify({"ok": True, "config": cfg, "scope": scope})

    # Route: /maintenance/api/test-rules
    @app.route("/maintenance/api/test-rules", methods=["POST"])
    def maintenance_api_test_rules():
        """Handle maintenance api test rules."""
        scope = _cleanup_normalize_scope(request.args.get("scope", "backups"))
        full_cfg = _cleanup_load_config(state)
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        preview = _cleanup_evaluate(state, cfg, mode="rule", apply_changes=False, trigger="tester")
        return jsonify({"ok": True, "preview": preview, "scope": scope})

    # Route: /maintenance/api/run-rules
    @app.route("/maintenance/api/run-rules", methods=["POST"])
    def maintenance_api_run_rules():
        """Handle maintenance api run rules."""
        payload = request.get_json(silent=True) or {}
        scope = _cleanup_normalize_scope(payload.get("scope", "backups"))
        sudo_password = str(payload.get("sudo_password", ""))
        selected_rule = str(payload.get("rule_key", "")).strip().lower()
        if selected_rule not in {"", "age", "count", "space"}:
            return _cleanup_error("validation_failure", "rule_key must be one of: age, count, space.", status=400)
        raw_dry_run = payload.get("dry_run", False)
        dry_run = bool(raw_dry_run)
        if isinstance(raw_dry_run, str):
            dry_run = raw_dry_run.strip().lower() in {"1", "true", "yes", "on"}
        if not state["validate_sudo_password"](sudo_password):
            _cleanup_log(state, what="run_rules", why="manual_apply", trigger="manual", result="invalid_password")
            return _cleanup_error("invalid_password", status=403)
        state["record_successful_password_ip"]()
        full_cfg = _cleanup_load_config(state)
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        if not cfg.get("rules", {}).get("enabled", True):
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
            preview = _cleanup_evaluate(state, eval_cfg, mode="rule", apply_changes=False, trigger="manual_rule")
            _cleanup_append_history(
                state,
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
                state,
                what="run_rules",
                why="manual_apply_dry_run",
                trigger="manual_rule",
                result="dry_run",
                details=f"scope={scope};rule={selected_rule or 'all'};requested={preview['requested_delete_count']};capped={preview['capped_delete_count']}",
            )
            return jsonify({"ok": True, "dry_run": True, "preview": preview, "config": cfg, "scope": scope})
        result = _cleanup_run_with_lock(state, eval_cfg, mode="rule", trigger="manual_rule")
        if result is None:
            return _cleanup_error("lock_held", status=409)
        meta = cfg.setdefault("meta", {})
        meta["last_run_at"] = _cleanup_now_iso(state)
        meta["last_run_trigger"] = "manual_rule"
        meta["last_run_result"] = "ok" if not result["errors"] else "partial"
        meta["last_run_deleted"] = result["deleted_count"]
        meta["last_run_errors"] = len(result["errors"])
        _cleanup_atomic_write_json(_cleanup_json_path(state), full_cfg)
        _cleanup_append_history(
            state,
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
            state,
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
        payload = request.get_json(silent=True) or {}
        scope = _cleanup_normalize_scope(payload.get("scope", "backups"))
        sudo_password = str(payload.get("sudo_password", ""))
        raw_dry_run = payload.get("dry_run", False)
        dry_run = bool(raw_dry_run)
        if isinstance(raw_dry_run, str):
            dry_run = raw_dry_run.strip().lower() in {"1", "true", "yes", "on"}
        if not state["validate_sudo_password"](sudo_password):
            _cleanup_log(state, what="manual_delete", why="manual_selection", trigger="manual", result="invalid_password")
            return _cleanup_error("invalid_password", status=403)
        state["record_successful_password_ip"]()
        selected = payload.get("selected_paths", [])
        if not isinstance(selected, list):
            return _cleanup_error("validation_failure", "selected_paths must be a list.", status=400)
        full_cfg = _cleanup_load_config(state)
        cfg = _cleanup_get_scope_view(full_cfg, scope)
        preview = _cleanup_evaluate(state, cfg, mode="manual", selected_paths=selected, apply_changes=False, trigger="manual_selection")
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
                state,
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
                state,
                what="manual_delete",
                why="manual_selection_dry_run",
                trigger="manual_selection",
                result="dry_run",
                details=f"scope={scope};selected={len(selected)};capped={preview['capped_delete_count']}",
            )
            return jsonify({"ok": True, "dry_run": True, "preview": preview, "config": cfg, "scope": scope})
        result = _cleanup_run_with_lock(state, cfg, mode="manual", selected_paths=selected, trigger="manual_selection")
        if result is None:
            return _cleanup_error("lock_held", status=409)
        meta = cfg.setdefault("meta", {})
        meta["last_run_at"] = _cleanup_now_iso(state)
        meta["last_run_trigger"] = "manual_selection"
        meta["last_run_result"] = "ok" if not result["errors"] else "partial"
        meta["last_run_deleted"] = result["deleted_count"]
        meta["last_run_errors"] = len(result["errors"])
        _cleanup_atomic_write_json(_cleanup_json_path(state), full_cfg)
        _cleanup_append_history(
            state,
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
            state,
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

        data = _cleanup_load_non_normal(state)
        missed = data.get("missed_runs")
        if not isinstance(missed, list):
            missed = []
        # Remove current-scope events and unknown-scope events (legacy/global).
        data["missed_runs"] = [item for item in missed if (_entry_scope(item) not in {"", scope})]
        data["last_ack_at"] = _cleanup_now_iso(state)
        data["last_ack_by"] = _cleanup_get_client_ip(state)
        _cleanup_atomic_write_json(_cleanup_non_normal_path(state), data)
        return jsonify({"ok": True, "non_normal": data})

