from zoneinfo import ZoneInfo
from types import SimpleNamespace

from app.commands import maintenance_commands


def test_cleanup_rejected_when_backup_running(tmp_path, monkeypatch):
    ctx = SimpleNamespace(APP_STATE_DB_PATH=tmp_path / "app_state.sqlite3", MCWEB_LOG_FILE=tmp_path / "mcweb.log", DISPLAY_TZ=ZoneInfo("UTC"))
    state = {
        "validate_sudo_password": lambda _pw: True,
        "record_successful_password_ip": lambda: None,
        "get_restore_status": lambda since_seq=0, job_id=None: {"running": False},
        "is_backup_running": lambda: True,
    }
    payload = {"scope": "backups", "dry_run": True}

    result = maintenance_commands.run_rules(ctx, state, payload)
    payload_out = result[0] if isinstance(result, tuple) else result

    assert payload_out["ok"] is False
    assert payload_out["error_code"] == "conflict"


def test_cleanup_rejected_when_restore_running(tmp_path, monkeypatch):
    ctx = SimpleNamespace(APP_STATE_DB_PATH=tmp_path / "app_state.sqlite3", MCWEB_LOG_FILE=tmp_path / "mcweb.log", DISPLAY_TZ=ZoneInfo("UTC"))
    state = {
        "validate_sudo_password": lambda _pw: True,
        "record_successful_password_ip": lambda: None,
        "get_restore_status": lambda since_seq=0, job_id=None: {"running": True},
        "is_backup_running": lambda: False,
    }
    payload = {"scope": "backups", "dry_run": True}

    result = maintenance_commands.run_rules(ctx, state, payload)
    payload_out = result[0] if isinstance(result, tuple) else result

    assert payload_out["ok"] is False
    assert payload_out["error_code"] == "conflict"
