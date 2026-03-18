from types import SimpleNamespace
import threading
import time
from zoneinfo import ZoneInfo

from app.services import dashboard_metrics_runtime as metrics_runtime
from app.services import maintenance_state_store as maintenance_state_store_service


def test_metrics_missed_runs_from_scheduler_gap(tmp_path, monkeypatch):
    session_file = tmp_path / "session.txt"
    session_file.write_text("session", encoding="utf-8")
    (tmp_path / "logs").mkdir(exist_ok=True)
    ctx = SimpleNamespace(
        DISPLAY_TZ=ZoneInfo("UTC"),
        OFF_STATES={"inactive", "failed"},
        BACKUP_DIR=str(tmp_path / "backups"),
        APP_STATE_DB_PATH=tmp_path / "app_state.sqlite3",
        MCWEB_LOG_FILE=tmp_path / "logs" / "mcweb.log",
        session_state=SimpleNamespace(session_file=session_file),
        metrics_cache_cond=threading.Condition(),
        metrics_cache_payload={},
        home_page_last_seen=0.0,
        file_page_last_seen=0.0,
        HOME_PAGE_ACTIVE_TTL_SECONDS=0.0,
        FILE_PAGE_ACTIVE_TTL_SECONDS=0.0,
        client_registry_lock=threading.Lock(),
        client_registry={},
        re=__import__("re"),
        get_status=lambda: "active",
        get_players_online=lambda: "0",
        get_tick_rate=lambda: "50ms",
        get_session_duration_text=lambda: "00:00:10",
        get_service_status_class=lambda _status: "stat-green",
        get_backup_status=lambda: ("Idle", "stat-yellow"),
        get_backup_warning_state=lambda _ttl: {"seq": 0, "message": ""},
        BACKUP_WARNING_TTL_SECONDS=120.0,
        get_backup_schedule_times=lambda _status: {"last_backup_time": "--", "next_backup_time": "--"},
        get_world_name=lambda: "World",
        is_rcon_enabled=lambda: True,
        get_idle_countdown=lambda _status, _players: "--:--",
        is_storage_low=lambda _usage: False,
        low_storage_error_message=lambda _usage=None: "",
    )

    now = int(time.time())
    maintenance_state_store_service._cleanup_record_scheduler_tick(ctx, "backups", now - 200, max_gap_seconds=75)
    maintenance_state_store_service._cleanup_record_scheduler_tick(ctx, "backups", now, max_gap_seconds=75)

    monkeypatch.setattr(
        metrics_runtime,
        "get_slow_metrics",
        lambda _ctx, _status, *, active_clients=False: {
            "cpu_per_core": ["10.0"],
            "ram_usage": "1/2 (50%)",
            "cpu_frequency": "3.2GHz",
            "storage_usage": "10/100 (10%)",
            "backups_status": "ok",
        },
    )
    monkeypatch.setattr(metrics_runtime, "get_observed_state", lambda _ctx: {"service_status_raw": "active"})
    monkeypatch.setattr(metrics_runtime, "get_backups_status", lambda _ctx: "ok")
    monkeypatch.setattr(metrics_runtime, "_get_backup_and_stale_counts", lambda _ctx: (0, 0, str(tmp_path / "backups")))
    monkeypatch.setattr(metrics_runtime.maintenance_scheduler_service, "get_next_cleanup_run_at", lambda _ctx, scope="backups": "")

    snapshot = metrics_runtime.collect_dashboard_metrics(ctx)

    assert snapshot["cleanup_missed_runs"] == 1
    assert snapshot["cleanup"]["missed_runs"] == 1
