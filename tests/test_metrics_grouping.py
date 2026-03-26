from types import SimpleNamespace
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pytest

from app.services import dashboard_metrics_runtime as metrics_runtime


def test_metrics_snapshot_grouping(monkeypatch):
    monkeypatch.setattr(
        metrics_runtime,
        "get_slow_metrics",
        lambda _ctx, _status, *, active_clients=False: {
            "cpu_per_core": ["10.0", "20.0"],
            "ram_usage": "1/2 (50%)",
            "cpu_frequency": "3.2GHz",
            "storage_usage": "10/100 (10%)",
            "backups_status": "ok",
        },
    )
    monkeypatch.setattr(metrics_runtime, "get_observed_state", lambda _ctx: {"service_status_raw": "active"})
    monkeypatch.setattr(metrics_runtime, "get_backups_status", lambda _ctx: "ok")
    monkeypatch.setattr(metrics_runtime.maintenance_state_store_service, "get_cleanup_meta", lambda _ctx, scope="backups": {
        "last_run_at": "2026-03-18T10:00:00",
        "rule_version": 2,
        "schedule_version": 3,
        "last_changed_by": "tester",
    })
    monkeypatch.setattr(metrics_runtime.maintenance_state_store_service, "get_cleanup_missed_run_count", lambda _ctx: 4)
    monkeypatch.setattr(metrics_runtime.maintenance_scheduler_service, "get_next_cleanup_run_at", lambda _ctx, scope="backups": "2026-03-18T12:00:00")
    monkeypatch.setattr(metrics_runtime, "_get_backup_and_stale_counts", lambda _ctx: (7, 2, "/backups"))

    ctx = SimpleNamespace(
        DISPLAY_TZ=ZoneInfo("UTC"),
        OFF_STATES={"inactive", "failed"},
        METRICS_COLLECT_INTERVAL_SECONDS=1.0,
        SLOW_METRICS_INTERVAL_ACTIVE_SECONDS=1.0,
        SLOW_METRICS_INTERVAL_OFF_SECONDS=15.0,
        metrics_cache_cond=type("DummyCond", (), {"__enter__": lambda self: self, "__exit__": lambda self, exc_type, exc, tb: None})(),
        metrics_stream_client_count=0,
        home_page_last_seen=0.0,
        file_page_last_seen=0.0,
        HOME_PAGE_ACTIVE_TTL_SECONDS=0.0,
        FILE_PAGE_ACTIVE_TTL_SECONDS=0.0,
        re=re,
        get_status=lambda: "active",
        get_players_online=lambda: "3",
        get_tick_rate=lambda: "50ms",
        get_session_duration_text=lambda: "00:01:00",
        get_service_status_class=lambda _status: "stat-green",
        get_backup_status=lambda: ("Idle", "stat-yellow"),
        get_backup_warning_state=lambda _ttl: {"seq": 0, "message": ""},
        BACKUP_WARNING_TTL_SECONDS=120.0,
        get_backup_schedule_times=lambda _status: {"last_backup_time": "yesterday", "next_backup_time": "soon"},
        get_world_name=lambda: "World",
        is_rcon_enabled=lambda: True,
        get_idle_countdown=lambda _status, _players: "--:--",
        is_storage_low=lambda _usage: False,
        low_storage_error_message=lambda _usage=None: "",
    )

    snapshot = metrics_runtime.collect_dashboard_metrics(ctx)

    assert snapshot["system"]["ram"] == snapshot["ram_usage"]
    assert snapshot["system"]["cpu"] == snapshot["cpu_per_core_items"]
    assert snapshot["system"]["freq"] == snapshot["cpu_frequency"]
    assert snapshot["system"]["storage"] == snapshot["storage_usage"]

    assert snapshot["minecraft"]["status"] == snapshot["service_status"]
    assert snapshot["minecraft"]["players"] == snapshot["players_online"]
    assert snapshot["minecraft"]["tick_time"] == snapshot["tick_rate"]
    assert snapshot["minecraft"]["auto_stop"] == snapshot["idle_countdown"]

    assert snapshot["backup"]["status"] == snapshot["backup_status"]
    assert snapshot["backup"]["last"] == snapshot["last_backup_time"]
    assert snapshot["backup"]["next"] == snapshot["next_backup_time"]
    assert snapshot["backup"]["count"] == snapshot["backup_files_count"]
    assert snapshot["backup"]["folder"] == snapshot["backup_folder"]

    assert snapshot["cleanup"]["last_run"] == snapshot["cleanup_last_run"]
    assert snapshot["cleanup"]["rule_version"] == snapshot["cleanup_rule_version"]
    assert snapshot["cleanup"]["schedule_version"] == snapshot["cleanup_schedule_version"]
    assert snapshot["cleanup"]["last_changed_by"] == snapshot["cleanup_last_changed_by"]
    assert snapshot["cleanup"]["missed_runs"] == snapshot["cleanup_missed_runs"]
    assert snapshot["cleanup"]["next_run"] == snapshot["cleanup_next_run"]
    assert snapshot["cleanup"]["stale_worlds_count"] == snapshot["stale_worlds_count"]


def test_active_clients_bypass_slow_metrics_cache():
    calls = {"cpu": 0, "ram": 0, "freq": 0, "storage": 0}

    class Ctx:
        slow_metrics_lock = type("DummyLock", (), {"__enter__": lambda self: self, "__exit__": lambda self, exc_type, exc, tb: False})()
        slow_metrics_cache = {
            "cpu_per_core": ["1.0"],
            "ram_usage": "old-ram",
            "cpu_frequency": "old-freq",
            "storage_usage": "old-storage",
            "backups_status": "old-backups",
        }
        slow_metrics_cache_status = "active"
        slow_metrics_cache_at = 9_999_999_999.0
        BACKUP_DIR = Path(".")

        @staticmethod
        def get_cpu_usage_per_core():
            calls["cpu"] += 1
            return ["20.0"]

        @staticmethod
        def get_ram_usage():
            calls["ram"] += 1
            return "new-ram"

        @staticmethod
        def get_cpu_frequency():
            calls["freq"] += 1
            return "new-freq"

        @staticmethod
        def get_storage_usage():
            calls["storage"] += 1
            return "new-storage"

    snapshot = metrics_runtime.get_slow_metrics(Ctx(), "active", active_clients=True)

    assert snapshot["cpu_per_core"] == ["20.0"]
    assert snapshot["cpu_frequency"] == "new-freq"
    assert calls == {"cpu": 1, "ram": 1, "freq": 1, "storage": 1}
