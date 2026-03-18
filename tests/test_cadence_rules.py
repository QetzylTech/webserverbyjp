import threading
from types import SimpleNamespace

import pytest

from app.services import dashboard_metrics_runtime as metrics_runtime
from app.services import log_stream_service


def _make_log_state(clients=0):
    return {
        "cond": threading.Condition(),
        "seq": 0,
        "events": [],
        "started": False,
        "lifecycle_lock": threading.Lock(),
        "clients": clients,
        "proc": None,
    }


def test_publish_log_stream_line_skips_db_without_clients(monkeypatch):
    calls = {"append": 0}

    def fake_append_event(_db_path, *, topic, payload):
        calls["append"] += 1
        return 1

    monkeypatch.setattr(log_stream_service.ports.store, "append_event", fake_append_event)

    ctx = SimpleNamespace(
        PROCESS_ROLE="all",
        APP_STATE_DB_PATH=":memory:",
        LOG_SOURCE_KEYS=("minecraft", "backup", "mcweb", "mcweb_log"),
        get_service_status_intent=lambda: "",
        RCON_STARTUP_READY_PATTERN=None,
        rcon_startup_lock=threading.Lock(),
        rcon_startup_ready=False,
        log_stream_states={"minecraft": _make_log_state(clients=0)},
        _append_minecraft_log_cache_line=lambda _line: None,
        _append_backup_log_cache_line=lambda _line: None,
        _append_mcweb_log_cache_line=lambda _line: None,
    )

    log_stream_service.publish_log_stream_line(ctx, "minecraft", "hello")

    assert calls["append"] == 0


def test_publish_log_stream_line_appends_db_with_clients(monkeypatch):
    calls = {"append": 0}

    def fake_append_event(_db_path, *, topic, payload):
        calls["append"] += 1
        return 42

    appended = {"lines": []}

    monkeypatch.setattr(log_stream_service.ports.store, "append_event", fake_append_event)

    ctx = SimpleNamespace(
        PROCESS_ROLE="all",
        APP_STATE_DB_PATH=":memory:",
        LOG_SOURCE_KEYS=("minecraft", "backup", "mcweb", "mcweb_log"),
        get_service_status_intent=lambda: "",
        RCON_STARTUP_READY_PATTERN=None,
        rcon_startup_lock=threading.Lock(),
        rcon_startup_ready=False,
        log_stream_states={"minecraft": _make_log_state(clients=1)},
        _append_minecraft_log_cache_line=lambda line: appended["lines"].append(line),
        _append_backup_log_cache_line=lambda _line: None,
        _append_mcweb_log_cache_line=lambda _line: None,
    )

    log_stream_service.publish_log_stream_line(ctx, "minecraft", "hello")

    assert calls["append"] == 1
    assert appended["lines"] == ["hello"]


def test_idle_storage_refresh_respects_interval(monkeypatch):
    ticks = {"now": 100.0}
    calls = {"storage": 0}

    def fake_time():
        return ticks["now"]

    def fake_storage():
        calls["storage"] += 1
        return "50%"

    ctx = SimpleNamespace(
        OFF_STATES={"inactive", "failed"},
        METRICS_IDLE_STORAGE_REFRESH_SECONDS=15.0,
        idle_storage_last_at=0.0,
        idle_storage_usage_text="",
        metrics_cache_cond=threading.Condition(),
        metrics_cache_payload={},
        re=__import__("re"),
        get_status=lambda: "active",
        get_storage_usage=fake_storage,
        log_mcweb_exception=lambda *_args, **_kwargs: None,
    )

    monkeypatch.setattr(metrics_runtime.time, "time", fake_time)

    monkeypatch.setattr(metrics_runtime, "_get_backup_and_stale_counts", lambda _ctx: (0, 0, ""))
    monkeypatch.setattr(metrics_runtime.maintenance_state_store_service, "get_cleanup_meta", lambda _ctx, scope="backups": {"last_run_at": "", "rule_version": 0, "schedule_version": 0, "last_changed_by": ""})
    monkeypatch.setattr(metrics_runtime.maintenance_state_store_service, "get_cleanup_missed_run_count", lambda _ctx: 0)
    monkeypatch.setattr(metrics_runtime.maintenance_scheduler_service, "get_next_cleanup_run_at", lambda _ctx, scope="backups": "")

    assert metrics_runtime._maybe_refresh_idle_storage_cache(ctx) is True
    assert calls["storage"] == 1

    ticks["now"] = 110.0
    assert metrics_runtime._maybe_refresh_idle_storage_cache(ctx) is False
    assert calls["storage"] == 1

    ticks["now"] = 116.0
    assert metrics_runtime._maybe_refresh_idle_storage_cache(ctx) is True
    assert calls["storage"] == 2


def test_metrics_collector_waits_for_clients_and_collects_when_active(monkeypatch):
    calls = []

    class DummyCond:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def wait_for(self, predicate, timeout=None):
            return predicate()

        def wait(self, timeout=None):
            return True

    active_sequence = iter([False, False, True, True])

    def fake_has_active(_ctx):
        return next(active_sequence)

    def fake_idle(_ctx):
        calls.append("idle")
        return True

    def fake_collect(_ctx):
        calls.append("collect")
        raise StopIteration()

    ctx = SimpleNamespace(
        PROCESS_ROLE="all",
        metrics_cache_cond=DummyCond(),
        METRICS_COLLECT_INTERVAL_SECONDS=1.0,
        SLOW_METRICS_INTERVAL_ACTIVE_SECONDS=5.0,
        SLOW_METRICS_INTERVAL_OFF_SECONDS=30.0,
    )

    monkeypatch.setattr(metrics_runtime, "has_active_flask_app_clients", fake_has_active)
    monkeypatch.setattr(metrics_runtime, "_maybe_refresh_idle_storage_cache", fake_idle)
    monkeypatch.setattr(metrics_runtime, "collect_and_publish_metrics", fake_collect)

    with pytest.raises(StopIteration):
        metrics_runtime.metrics_collector_loop(ctx)

    assert calls == ["idle", "collect"]
