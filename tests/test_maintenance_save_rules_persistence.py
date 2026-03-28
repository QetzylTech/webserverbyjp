from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.commands import maintenance_commands
from app.core import state_store as state_store_service
from app.services.maintenance_state_store import _cleanup_default_config
from unittest.mock import patch


def test_save_rules_refreshes_persisted_maintenance_state_event(tmp_path):
    session_file = tmp_path / "session.txt"
    session_file.write_text("session", encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    db_path = tmp_path / "app_state.sqlite3"
    state_store_service.initialize_state_db(db_path=db_path)
    state_store_service.append_event(
        db_path,
        topic="maintenance_state:backups",
        payload={
            "scope": "backups",
            "snapshot": {"config": {"rules": {"age": {"days": 3}}}},
            "preview": {"requested_delete_count": 0},
        },
    )
    ctx = SimpleNamespace(
        DISPLAY_TZ=ZoneInfo("UTC"),
        BACKUP_DIR=backup_dir,
        APP_STATE_DB_PATH=db_path,
        MCWEB_LOG_FILE=log_dir / "mcweb.log",
        session_state=SimpleNamespace(session_file=session_file),
        MAINTENANCE_SCOPE_BACKUP_ZIP=True,
        MAINTENANCE_SCOPE_STALE_WORLD_DIR=True,
        MAINTENANCE_SCOPE_OLD_WORLD_ZIP=True,
        MAINTENANCE_GUARD_NEVER_DELETE_NEWEST_N=1,
        MAINTENANCE_GUARD_NEVER_DELETE_LAST_BACKUP=True,
        MAINTENANCE_GUARD_PROTECT_ACTIVE_WORLD=True,
        log_mcweb_exception=lambda *args, **kwargs: None,
        _get_client_ip=lambda: "100.64.0.9",
    )
    state = {
        "validate_sudo_password": lambda password: password == "ok",
        "record_successful_password_ip": lambda: None,
    }
    rules = _cleanup_default_config()["rules"]
    rules["age"]["days"] = 7

    with patch.object(
        maintenance_commands,
        "_cleanup_evaluate",
        return_value={"requested_delete_count": 0, "capped_delete_count": 0},
    ), patch.object(
        maintenance_commands,
        "_cleanup_state_snapshot",
        side_effect=lambda _ctx, cfg: {"config": cfg},
    ):
        result = maintenance_commands.save_rules(
            ctx,
            state,
            {"scope": "backups", "sudo_password": "ok", "rules": rules},
        )

    assert result["ok"] is True
    event = state_store_service.get_latest_event(db_path, topic="maintenance_state:backups")
    assert event is not None
    payload = event.get("payload") or {}
    assert payload.get("snapshot", {}).get("config", {}).get("rules", {}).get("age", {}).get("days") == 7
