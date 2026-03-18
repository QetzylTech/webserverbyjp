from types import SimpleNamespace
import threading

import pytest

from app.commands import control_handlers


def _make_ctx(state):
    return SimpleNamespace(state=state, process_role="all", run_cleanup_event_if_enabled=lambda *_a, **_k: None)


def test_restore_rejected_when_server_running(tmp_path, monkeypatch):
    state = {
        "APP_STATE_DB_PATH": tmp_path / "app_state.sqlite3",
        "validate_sudo_password": lambda password: True,
        "record_successful_password_ip": lambda: None,
        "get_status": lambda: "active",
        "get_service_status_intent": lambda: "",
        "OFF_STATES": {"inactive", "failed"},
        "log_mcweb_action": lambda *_a, **_k: None,
        "is_backup_running": lambda: False,
    }
    ctx = _make_ctx(state)

    result = control_handlers.restore_operation(
        ctx,
        idempotency_key="",
        client_key="client",
        sudo_password="ok",
        filename="backup.zip",
    )

    assert result.status_code == 409
    assert result.payload["error"] == "invalid_state"


def test_restore_rejected_when_backup_running(tmp_path, monkeypatch):
    state = {
        "APP_STATE_DB_PATH": tmp_path / "app_state.sqlite3",
        "validate_sudo_password": lambda password: True,
        "record_successful_password_ip": lambda: None,
        "get_status": lambda: "inactive",
        "get_service_status_intent": lambda: "",
        "OFF_STATES": {"inactive", "failed"},
        "log_mcweb_action": lambda *_a, **_k: None,
        "is_backup_running": lambda: True,
    }
    ctx = _make_ctx(state)

    monkeypatch.setattr(control_handlers, "_has_pending_operation", lambda *_a, **_k: False)
    monkeypatch.setattr(control_handlers.maintenance_engine_service, "cleanup_lock_held", lambda: False)

    result = control_handlers.restore_operation(
        ctx,
        idempotency_key="",
        client_key="client",
        sudo_password="ok",
        filename="backup.zip",
    )

    assert result.status_code == 409
    assert result.payload["error"] == "invalid_state"
