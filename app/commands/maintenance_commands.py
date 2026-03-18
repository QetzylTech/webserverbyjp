"""Command-side maintenance helpers used by maintenance and control routes."""

from __future__ import annotations

import copy

from app.services.maintenance_engine import _cleanup_evaluate, _cleanup_run_with_lock
from app.core import state_store as state_store_service
from app.services.maintenance_policy import _cleanup_validate_rules
from app.services.maintenance_scheduler import run_cleanup_event_if_enabled, start_cleanup_scheduler_once
from app.services.maintenance_state_store import (
    _cleanup_append_history,
    _cleanup_apply_scope_from_state,
    _cleanup_atomic_write_json,
    _cleanup_error,
    _cleanup_get_client_ip,
    _cleanup_get_scope_view,
    _cleanup_load_config,
    _cleanup_load_non_normal,
    _cleanup_log,
    _cleanup_non_normal_path,
    _cleanup_normalize_scope,
    _cleanup_now_iso,
    _cleanup_save_config,
)


def normalize_scope(raw_scope):
    return _cleanup_normalize_scope(raw_scope)


def _has_pending_operation(ctx, op_type):
    db_path = getattr(ctx, "APP_STATE_DB_PATH", None)
    if db_path is None:
        return False
    try:
        rows = state_store_service.list_operations_by_status(
            db_path,
            statuses=("intent", "in_progress"),
            limit=80,
        )
    except Exception:
        return False
    kind = str(op_type or "").strip().lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("op_type", "") or "").strip().lower() == kind:
            return True
    return False


def _restore_running(state):
    getter = state.get("get_restore_status") if isinstance(state, dict) else None
    if not callable(getter):
        return False
    try:
        payload = getter(since_seq=0, job_id=None)
    except Exception:
        return False
    return bool(payload.get("running")) if isinstance(payload, dict) else False


def _priority_conflict(ctx, state):
    if state.get("is_backup_running", lambda: False)():
        return "backup_running"
    if _has_pending_operation(ctx, "backup"):
        return "backup_queued"
    if _restore_running(state):
        return "restore_running"
    if _has_pending_operation(ctx, "restore"):
        return "restore_queued"
    return ""


def _require_password(state, payload, *, what, why, trigger, scope, details='', log_success=False):
    sudo_password = str(payload.get('sudo_password', ''))
    if state['validate_sudo_password'](sudo_password):
        state['record_successful_password_ip']()
        if log_success:
            _cleanup_log(
                state,
                what=what,
                why=why,
                trigger=trigger,
                result='ok',
                details=f"scope={scope};{details}".strip(';'),
            )
        return True, None
    _cleanup_log(
        state,
        what=what,
        why=why,
        trigger=trigger,
        result='invalid_password',
        details=f"scope={scope};{details}".strip(';'),
    )
    return False, _cleanup_error('invalid_password', status=403)


def confirm_password(state, payload):
    scope = normalize_scope(payload.get('scope', 'backups'))
    action = str(payload.get('action', '')).strip().lower()
    action_map = {
        'open_rules_edit': ('confirm_password', 'open_rules_edit', 'manual'),
        'save_rules': ('confirm_password', 'save_rules', 'manual'),
        'run_rules': ('confirm_password', 'run_rules', 'manual'),
        'manual_delete': ('confirm_password', 'manual_delete', 'manual'),
    }
    if action not in action_map:
        return _cleanup_error('validation_failure', 'Unsupported action.', status=400)
    what, why, trigger = action_map[action]
    ok, err = _require_password(
        state,
        payload,
        what=what,
        why=why,
        trigger=trigger,
        scope=scope,
        details=f'action={action}',
        log_success=True,
    )
    if not ok:
        return err
    return {'ok': True, 'scope': scope, 'action': action}


def save_rules(ctx, state, payload):
    scope = normalize_scope(payload.get('scope', 'backups'))
    ok_pw, err = _require_password(payload=payload, state=state, what='save_rules', why='manual_save', trigger='manual', scope=scope)
    if not ok_pw:
        return err
    ok, parsed = _cleanup_validate_rules(payload.get('rules', {}))
    if not ok:
        _cleanup_log(ctx, what='save_rules', why='manual_save', trigger='manual', result='validation_failure', details=f'scope={scope};error={parsed}')
        return _cleanup_error('validation_failure', parsed, status=400)
    full_cfg = _cleanup_load_config(ctx)
    cfg = _cleanup_get_scope_view(full_cfg, scope)
    cfg['rules'] = _cleanup_apply_scope_from_state(ctx, parsed, scope=scope)
    time_based = cfg.get('rules', {}).get('time_based', {})
    time_enabled = bool(time_based.get('enabled', True))
    repeat_mode = str(time_based.get('repeat_mode', 'does_not_repeat')).strip().lower()
    if not time_enabled or repeat_mode == 'does_not_repeat':
        cfg['schedules'] = []
    else:
        interval_map = {
            'daily': 'daily',
            'weekly': 'weekly',
            'monthly': 'monthly',
            'weekdays': 'weekdays',
            'every_n_days': 'every_n_days',
        }
        weekly_day_map = {
            'Sunday': 6,
            'Monday': 0,
            'Tuesday': 1,
            'Wednesday': 2,
            'Thursday': 3,
            'Friday': 4,
            'Saturday': 5,
        }
        cfg['schedules'] = [{
            'id': 'time-based-rule',
            'mode': 'time',
            'enabled': True,
            'interval': interval_map.get(repeat_mode, 'daily'),
            'time': str(time_based.get('time_of_backup', '03:00')),
            'day_of_week': int(weekly_day_map.get(str(time_based.get('weekly_day', 'Sunday')), 6)),
            'day_of_month': int(time_based.get('monthly_date', 1)),
            'every_n_days': int(time_based.get('every_n_days', 1)),
            'anchor_date': _cleanup_now_iso(ctx)[:10],
        }]
    meta = cfg.setdefault('meta', {})
    meta['rule_version'] = int(meta.get('rule_version', 0)) + 1
    meta['schedule_version'] = int(meta.get('schedule_version', 0)) + 1
    meta['last_changed_by'] = _cleanup_get_client_ip(ctx)
    meta['last_changed_at'] = _cleanup_now_iso(ctx)
    _cleanup_save_config(ctx, full_cfg)
    _cleanup_log(
        state,
        what='save_rules',
        why='manual_save',
        trigger='manual',
        result='ok',
        details=f"scope={scope};rule_version={meta['rule_version']}",
    )
    preview = _cleanup_evaluate(ctx, cfg, mode='rule', apply_changes=False, trigger='preview')
    return {'ok': True, 'config': cfg, 'preview': preview, 'scope': scope}


def _parse_dry_run(value):
    dry_run = bool(value)
    if isinstance(value, str):
        dry_run = value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return dry_run


def run_rules(ctx, state, payload):
    scope = normalize_scope(payload.get('scope', 'backups'))
    conflict_reason = _priority_conflict(ctx, state)
    if conflict_reason:
        _cleanup_log(ctx, what='run_rules', why='manual_apply', trigger='manual_rule', result='conflict', details=f"scope={scope};reason={conflict_reason}")
        return _cleanup_error('conflict', status=409)
    selected_rule = str(payload.get('rule_key', '')).strip().lower()
    if selected_rule not in {'', 'age', 'count', 'space'}:
        return _cleanup_error('validation_failure', 'rule_key must be one of: age, count, space.', status=400)
    dry_run = _parse_dry_run(payload.get('dry_run', False))
    full_cfg = _cleanup_load_config(ctx)
    cfg = _cleanup_get_scope_view(full_cfg, scope)
    if not cfg.get('rules', {}).get('enabled', True):
        _cleanup_log(ctx, what='run_rules', why='manual_apply', trigger='manual_rule', result='rules_disabled', details=f'scope={scope}')
        return _cleanup_error('rules_disabled', status=400)
    eval_cfg = cfg
    if selected_rule:
        eval_cfg = copy.deepcopy(cfg)
        rules = eval_cfg.setdefault('rules', {})
        rules['enabled'] = True
        for key in ('age', 'count', 'space'):
            sub = rules.setdefault(key, {})
            sub['enabled'] = key == selected_rule
    if dry_run:
        preview = _cleanup_evaluate(ctx, eval_cfg, mode='rule', apply_changes=False, trigger='manual_rule')
        _cleanup_append_history(
            ctx,
            trigger=f"manual_rule:{selected_rule or 'all'}",
            mode='rule',
            dry_run=True,
            deleted_count=0,
            errors_count=0,
            requested_count=preview.get('requested_delete_count', 0),
            capped_count=preview.get('capped_delete_count', 0),
            result='dry_run',
            scope=scope,
        )
        _cleanup_log(
            ctx,
            what='run_rules',
            why='manual_apply_dry_run',
            trigger='manual_rule',
            result='dry_run',
            details=f"scope={scope};rule={selected_rule or 'all'};requested={preview['requested_delete_count']};capped={preview['capped_delete_count']}",
        )
        return {'ok': True, 'dry_run': True, 'preview': preview, 'config': cfg, 'scope': scope}
    ok_pw, err = _require_password(payload=payload, state=state, what='run_rules', why='manual_apply', trigger='manual', scope=scope)
    if not ok_pw:
        return err
    result = _cleanup_run_with_lock(ctx, eval_cfg, mode='rule', trigger='manual_rule')
    if result is None:
        _cleanup_log(ctx, what='run_rules', why='manual_apply', trigger='manual_rule', result='lock_held', details=f'scope={scope}')
        return _cleanup_error('lock_held', status=409)
    meta = cfg.setdefault('meta', {})
    meta['last_run_at'] = _cleanup_now_iso(ctx)
    meta['last_run_trigger'] = 'manual_rule'
    meta['last_run_result'] = 'ok' if not result['errors'] else 'partial'
    meta['last_run_deleted'] = result['deleted_count']
    meta['last_run_errors'] = len(result['errors'])
    _cleanup_save_config(ctx, full_cfg)
    _cleanup_append_history(
        ctx,
        trigger=f"manual_rule:{selected_rule or 'all'}",
        mode='rule',
        dry_run=False,
        deleted_count=result['deleted_count'],
        errors_count=len(result['errors']),
        requested_count=result.get('requested_delete_count', 0),
        capped_count=result.get('capped_delete_count', result['deleted_count']),
        result=meta['last_run_result'],
        scope=scope,
    )
    _cleanup_log(
        ctx,
        what='run_rules',
        why='manual_apply',
        trigger='manual_rule',
        result=meta['last_run_result'],
        details=f"scope={scope};rule={selected_rule or 'all'};deleted={result['deleted_count']};errors={len(result['errors'])}",
    )
    return {'ok': True, 'result': result, 'config': cfg, 'scope': scope}


def manual_delete(ctx, state, payload):
    scope = normalize_scope(payload.get('scope', 'backups'))
    conflict_reason = _priority_conflict(ctx, state)
    if conflict_reason:
        _cleanup_log(ctx, what='manual_delete', why='manual_selection', trigger='manual', result='conflict', details=f"scope={scope};reason={conflict_reason}")
        return _cleanup_error('conflict', status=409)
    dry_run = _parse_dry_run(payload.get('dry_run', False))
    selected = payload.get('selected_paths', [])
    if not isinstance(selected, list):
        return _cleanup_error('validation_failure', 'selected_paths must be a list.', status=400)
    full_cfg = _cleanup_load_config(ctx)
    cfg = _cleanup_get_scope_view(full_cfg, scope)
    preview = _cleanup_evaluate(ctx, cfg, mode='manual', selected_paths=selected, apply_changes=False, trigger='manual_selection')
    if preview['selected_ineligible']:
        _cleanup_log(
            state,
            what='manual_delete',
            why='manual_selection',
            trigger='manual',
            result='ineligible_selection',
            details=f"count={len(preview['selected_ineligible'])}",
        )
        return _cleanup_error('ineligible_selection', {'paths': preview['selected_ineligible']}, status=409)
    if dry_run:
        _cleanup_append_history(
            ctx,
            trigger='manual_selection',
            mode='manual',
            dry_run=True,
            deleted_count=0,
            errors_count=0,
            requested_count=preview.get('requested_delete_count', 0),
            capped_count=preview.get('capped_delete_count', 0),
            result='dry_run',
            scope=scope,
        )
        _cleanup_log(
            ctx,
            what='manual_delete',
            why='manual_selection_dry_run',
            trigger='manual_selection',
            result='dry_run',
            details=f"scope={scope};selected={len(selected)};capped={preview['capped_delete_count']}",
        )
        return {'ok': True, 'dry_run': True, 'preview': preview, 'config': cfg, 'scope': scope}
    ok_pw, err = _require_password(payload=payload, state=state, what='manual_delete', why='manual_selection', trigger='manual', scope=scope)
    if not ok_pw:
        return err
    result = _cleanup_run_with_lock(ctx, cfg, mode='manual', selected_paths=selected, trigger='manual_selection')
    if result is None:
        _cleanup_log(ctx, what='manual_delete', why='manual_selection', trigger='manual_selection', result='lock_held', details=f'scope={scope}')
        return _cleanup_error('lock_held', status=409)
    meta = cfg.setdefault('meta', {})
    meta['last_run_at'] = _cleanup_now_iso(ctx)
    meta['last_run_trigger'] = 'manual_selection'
    meta['last_run_result'] = 'ok' if not result['errors'] else 'partial'
    meta['last_run_deleted'] = result['deleted_count']
    meta['last_run_errors'] = len(result['errors'])
    _cleanup_save_config(ctx, full_cfg)
    _cleanup_append_history(
        ctx,
        trigger='manual_selection',
        mode='manual',
        dry_run=False,
        deleted_count=result['deleted_count'],
        errors_count=len(result['errors']),
        requested_count=result.get('requested_delete_count', 0),
        capped_count=result.get('capped_delete_count', result['deleted_count']),
        result=meta['last_run_result'],
        scope=scope,
    )
    _cleanup_log(
        ctx,
        what='manual_delete',
        why='manual_selection',
        trigger='manual_selection',
        result=meta['last_run_result'],
        details=f"scope={scope};deleted={result['deleted_count']};errors={len(result['errors'])}",
    )
    return {'ok': True, 'result': result, 'config': cfg, 'scope': scope}


def ack_non_normal(ctx, payload):
    scope = normalize_scope(payload.get('scope', 'backups'))

    def _entry_scope(entry):
        if not isinstance(entry, dict):
            return ''
        raw_scope = str(entry.get('scope', '')).strip().lower()
        if raw_scope in {'backups', 'stale_worlds'}:
            return raw_scope
        schedule_id = str(entry.get('schedule_id', '')).strip().lower()
        if schedule_id.startswith('backups:'):
            return 'backups'
        if schedule_id.startswith('stale_worlds:'):
            return 'stale_worlds'
        return ''

    data = _cleanup_load_non_normal(ctx)
    missed = data.get('missed_runs')
    if not isinstance(missed, list):
        missed = []
    data['missed_runs'] = [item for item in missed if (_entry_scope(item) not in {'', scope})]
    data['last_ack_at'] = _cleanup_now_iso(ctx)
    data['last_ack_by'] = _cleanup_get_client_ip(ctx)
    _cleanup_atomic_write_json(_cleanup_non_normal_path(ctx), data)
    _cleanup_log(ctx, what='ack_non_normal', why='manual_ack', trigger='manual', result='ok', details=f'scope={scope}')
    return {'ok': True, 'non_normal': data, 'scope': scope}


__all__ = [
    'normalize_scope',
    'start_cleanup_scheduler_once',
    'run_cleanup_event_if_enabled',
    'confirm_password',
    'save_rules',
    'run_rules',
    'manual_delete',
    'ack_non_normal',
]
