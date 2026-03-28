import threading
import tempfile
from types import SimpleNamespace
from pathlib import Path
import re

import pytest
from flask import Flask

from app.routes import dashboard_metrics_routes
from app.services import dashboard_metrics_runtime as metrics_runtime
from app.services import log_stream_service
from app.services import session_watchers
from app.state import REQUIRED_STATE_KEY_SET


def _make_log_state(clients=0):
    return {
        "cond": threading.Condition(),
        "seq": 0,
        "events": [],
        "buffered_lines": [],
        "pending_lines": [],
        "pending_bytes": 0,
        "batch_started_at": 0.0,
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
        LOG_STREAM_BATCH_MAX_LINES=1,
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
        LOG_STREAM_BATCH_MAX_LINES=1,
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
    assert ctx.log_stream_states["minecraft"]["events"][0][1]["lines"] == ["hello"]


def test_publish_log_stream_line_marks_start_observed_from_startup_log(monkeypatch):
    recorded = {"updated": None}

    def fake_get_latest_operation_for_type(_db_path, op_type):
        assert op_type == "start"
        return {"op_id": "start-1", "status": "in_progress"}

    def fake_update_operation(_db_path, **kwargs):
        recorded["updated"] = kwargs

    monkeypatch.setattr(log_stream_service.state_store_service, "get_latest_operation_for_type", fake_get_latest_operation_for_type)
    monkeypatch.setattr(log_stream_service.state_store_service, "update_operation", fake_update_operation)

    calls = {"intent": [], "status_cache": 0, "observed_cache": 0}
    ctx = SimpleNamespace(
        PROCESS_ROLE="all",
        APP_STATE_DB_PATH=":memory:",
        LOG_SOURCE_KEYS=("minecraft", "backup", "mcweb", "mcweb_log"),
        LOG_STREAM_BATCH_MAX_LINES=1,
        get_service_status_intent=lambda: "starting",
        set_service_status_intent=lambda value: calls["intent"].append(value),
        invalidate_status_cache=lambda: calls.__setitem__("status_cache", calls["status_cache"] + 1),
        invalidate_observed_state_cache=lambda: calls.__setitem__("observed_cache", calls["observed_cache"] + 1),
        RCON_STARTUP_READY_PATTERN=re.compile(r"Dedicated server took\s+\d+(?:[.,]\d+)?\s+seconds to load", re.IGNORECASE),
        rcon_startup_lock=threading.Lock(),
        rcon_startup_ready=False,
        log_stream_states={"minecraft": _make_log_state(clients=0)},
        _append_minecraft_log_cache_line=lambda _line: None,
        _append_backup_log_cache_line=lambda _line: None,
        _append_mcweb_log_cache_line=lambda _line: None,
        log_mcweb_exception=lambda *_args, **_kwargs: None,
    )

    log_stream_service.publish_log_stream_line(ctx, "minecraft", "Dedicated server took 88.715 seconds to load")

    assert ctx.rcon_startup_ready is True
    assert calls["intent"] == [None]
    assert calls["status_cache"] == 1
    assert calls["observed_cache"] == 1
    assert recorded["updated"] is not None
    assert recorded["updated"]["op_id"] == "start-1"
    assert recorded["updated"]["status"] == "observed"


def test_flush_log_stream_batch_groups_multiple_lines_into_one_payload(monkeypatch):
    appended = []

    def fake_append_event(_db_path, *, topic, payload):
        appended.append((topic, payload))
        return 7

    monkeypatch.setattr(log_stream_service.ports.store, "append_event", fake_append_event)

    ctx = SimpleNamespace(
        PROCESS_ROLE="all",
        APP_STATE_DB_PATH=":memory:",
        LOG_SOURCE_KEYS=("minecraft", "backup", "mcweb", "mcweb_log"),
        LOG_STREAM_BATCH_MAX_LINES=99,
        get_service_status_intent=lambda: "",
        RCON_STARTUP_READY_PATTERN=None,
        rcon_startup_lock=threading.Lock(),
        rcon_startup_ready=False,
        log_stream_states={"minecraft": _make_log_state(clients=1)},
        _append_minecraft_log_cache_line=lambda _line: None,
        _append_backup_log_cache_line=lambda _line: None,
        _append_mcweb_log_cache_line=lambda _line: None,
    )

    log_stream_service.publish_log_stream_line(ctx, "minecraft", "one")
    log_stream_service.publish_log_stream_line(ctx, "minecraft", "two")

    assert appended == []
    assert log_stream_service.flush_log_stream_batch(ctx, "minecraft", force=True) is True
    assert appended == [("log:minecraft", {"source": "minecraft", "lines": ["one", "two"]})]


def test_metrics_stream_refreshes_once_on_connect_then_waits(monkeypatch):
    calls = {"refresh": 0}

    class DummyCond:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def notify_all(self):
            return None

        def wait_for(self, predicate, timeout=None):
            return predicate()

    app = Flask(__name__)
    state = {
        "APP_STATE_DB_PATH": ":memory:",
        "metrics_cache_cond": DummyCond(),
        "metrics_stream_client_count": 0,
        "metrics_cache_seq": 1,
        "metrics_cache_payload": {"service_status": "Off"},
        "METRICS_STREAM_HEARTBEAT_SECONDS": 1.0,
        "get_cached_dashboard_metrics": lambda: {"service_status": "Off"},
        "ensure_metrics_collector_started": lambda: None,
        "_collect_and_publish_metrics": lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
    }

    dashboard_metrics_routes.register_metrics_routes(app, state)

    monkeypatch.setattr(
        dashboard_metrics_routes.state_store_service,
        "get_latest_event",
        lambda _db_path, topic="metrics_snapshot": {"id": 1, "payload": {"snapshot": {"service_status": "Off"}}},
    )
    monkeypatch.setattr(
        dashboard_metrics_routes.state_store_service,
        "list_events_since",
        lambda *_args, **_kwargs: [],
    )

    with app.test_request_context("/metrics-stream"):
        response = app.view_functions["metrics_stream"]()
        try:
            first_chunk = next(response.response)
            second_chunk = next(response.response)
        finally:
            response.close()

    assert "service_status" in first_chunk
    assert second_chunk == ": keepalive\n\n"
    assert calls["refresh"] == 1


def test_metrics_stream_emits_cache_updates_even_when_db_event_ids_are_ahead(monkeypatch):
    state = {
        "APP_STATE_DB_PATH": ":memory:",
        "metrics_stream_client_count": 0,
        "metrics_cache_seq": 1,
        "metrics_cache_payload": {"service_status": "Off"},
        "METRICS_STREAM_HEARTBEAT_SECONDS": 1.0,
        "get_cached_dashboard_metrics": lambda: {"service_status": "Off"},
        "ensure_metrics_collector_started": lambda: None,
        "_collect_and_publish_metrics": lambda: None,
    }

    class DummyCond:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def notify_all(self):
            return None

        def wait_for(self, predicate, timeout=None):
            state["metrics_cache_seq"] = 2
            state["metrics_cache_payload"] = {"service_status": "Running"}
            return predicate()

    state["metrics_cache_cond"] = DummyCond()

    app = Flask(__name__)
    dashboard_metrics_routes.register_metrics_routes(app, state)

    monkeypatch.setattr(
        dashboard_metrics_routes.state_store_service,
        "get_latest_event",
        lambda _db_path, topic="metrics_snapshot": {"id": 10, "payload": {"snapshot": {"service_status": "Off"}}},
    )
    monkeypatch.setattr(
        dashboard_metrics_routes.state_store_service,
        "list_events_since",
        lambda *_args, **_kwargs: [],
    )

    with app.test_request_context("/metrics-stream"):
        response = app.view_functions["metrics_stream"]()
        try:
            first_chunk = next(response.response)
            second_chunk = next(response.response)
            third_chunk = next(response.response)
        finally:
            response.close()

    assert "service_status" in first_chunk
    assert second_chunk == ": keepalive\n\n"
    assert "Running" in third_chunk


def test_metrics_stream_reads_runtime_context_when_state_mapping_is_stale(monkeypatch):
    class StateWithCtx(dict):
        pass

    state = StateWithCtx(
        {
            "APP_STATE_DB_PATH": ":memory:",
            "metrics_stream_client_count": 0,
            "metrics_cache_seq": 0,
            "metrics_cache_payload": {},
            "METRICS_STREAM_HEARTBEAT_SECONDS": 1.0,
            "get_cached_dashboard_metrics": lambda: {"service_status": "Off"},
            "ensure_metrics_collector_started": lambda: None,
            "_collect_and_publish_metrics": lambda: None,
        }
    )

    class DummyCond:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def notify_all(self):
            return None

        def wait_for(self, predicate, timeout=None):
            return predicate()

    state.ctx = SimpleNamespace(
        metrics_cache_cond=DummyCond(),
        metrics_stream_client_count=0,
        metrics_cache_seq=3,
        metrics_cache_payload={"service_status": "Running"},
    )

    app = Flask(__name__)
    dashboard_metrics_routes.register_metrics_routes(app, state)

    monkeypatch.setattr(
        dashboard_metrics_routes.state_store_service,
        "get_latest_event",
        lambda _db_path, topic="metrics_snapshot": None,
    )
    monkeypatch.setattr(
        dashboard_metrics_routes.state_store_service,
        "list_events_since",
        lambda *_args, **_kwargs: [],
    )

    with app.test_request_context("/metrics-stream"):
        response = app.view_functions["metrics_stream"]()
        try:
            first_chunk = next(response.response)
        finally:
            response.close()

    assert "Running" in first_chunk


def test_minecraft_log_source_prefers_journal_even_when_latest_file_exists(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        logs_dir = Path(tmp)
        (logs_dir / "latest.log").write_text("line\n", encoding="utf-8")
        monkeypatch.setattr(log_stream_service.ports.log, "minecraft_log_stream_mode", lambda: "journal")
        ctx = SimpleNamespace(
            LOG_SOURCE_KEYS=("minecraft",),
            MINECRAFT_LOGS_DIR=logs_dir,
            MINECRAFT_LOG_TEXT_LIMIT=1000,
            SERVICE="minecraft",
        )

        settings = log_stream_service.log_source_settings(ctx, "minecraft")

    assert settings is not None
    assert settings["type"] == "journal"


def test_increment_log_stream_clients_notifies_waiters():
    state = _make_log_state(clients=0)
    ctx = SimpleNamespace(
        LOG_SOURCE_KEYS=("minecraft",),
        log_stream_states={"minecraft": state},
    )
    wake = {"notified": False}

    def waiter():
        with state["cond"]:
            state["cond"].wait(timeout=1.0)
            wake["notified"] = True

    thread = threading.Thread(target=waiter)
    thread.start()
    log_stream_service.increment_log_stream_clients(ctx, "minecraft")
    thread.join(timeout=2.0)

    assert wake["notified"] is True


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


def test_has_active_flask_app_clients_counts_open_metrics_stream_without_registry():
    ctx = SimpleNamespace(
        metrics_cache_cond=threading.Condition(),
        metrics_stream_client_count=1,
        home_page_last_seen=0.0,
        file_page_last_seen=0.0,
        HOME_PAGE_ACTIVE_TTL_SECONDS=0.0,
        FILE_PAGE_ACTIVE_TTL_SECONDS=0.0,
        client_registry={},
        client_registry_lock=threading.Lock(),
    )

    assert metrics_runtime.has_active_flask_app_clients(ctx) is True


def test_app_state_contract_includes_metrics_collector_starter_binding():
    assert "ensure_metrics_collector_started" in REQUIRED_STATE_KEY_SET


def test_app_state_contract_includes_backup_session_watcher_starters():
    assert "start_backup_session_watcher" in REQUIRED_STATE_KEY_SET
    assert "start_idle_player_watcher" in REQUIRED_STATE_KEY_SET
    assert "initialize_session_tracking" in REQUIRED_STATE_KEY_SET


def test_backup_session_watcher_runs_auto_backup_when_interval_due(monkeypatch):
    backup_state = SimpleNamespace(lock=threading.Lock(), periodic_runs=0)
    calls = []

    ctx = SimpleNamespace(
        backup_state=backup_state,
        BACKUP_INTERVAL_SECONDS=300,
        BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS=5,
        BACKUP_WATCH_INTERVAL_OFF_SECONDS=15,
        get_status=lambda: "active",
        read_session_start_time=lambda: 100.0,
        run_backup_script=lambda count_skip_as_success=False, trigger="manual": (calls.append((count_skip_as_success, trigger)) or True),
        clear_session_start_time=lambda: None,
        reset_backup_schedule_state=lambda: None,
        log_mcweb_exception=lambda *_args, **_kwargs: None,
    )

    monkeypatch.setattr(session_watchers.time, "time", lambda: 450.0)

    def stop_after_one_loop(_seconds):
        raise StopIteration()

    monkeypatch.setattr(session_watchers.time, "sleep", stop_after_one_loop)

    with pytest.raises(StopIteration):
        session_watchers.backup_session_watcher(ctx)

    assert calls == [(False, "auto")]
    assert backup_state.periodic_runs == 1


def test_ensure_metrics_collector_restarts_when_health_is_not_running(monkeypatch):
    started = {"count": 0}

    def fake_start_worker(_ctx, _spec):
        started["count"] += 1

    monkeypatch.setattr(
        metrics_runtime,
        "get_worker_health_snapshot",
        lambda: {"metrics_collector": {"running": False}},
    )
    monkeypatch.setattr(metrics_runtime, "start_worker", fake_start_worker)

    ctx = SimpleNamespace(
        metrics_collector_started=True,
        metrics_collector_start_lock=threading.Lock(),
        METRICS_COLLECT_INTERVAL_SECONDS=1.0,
    )

    metrics_runtime.ensure_metrics_collector_started(ctx)

    assert started["count"] == 1
    assert ctx.metrics_collector_started is True


def test_ensure_log_stream_fetcher_restarts_when_health_is_not_running(monkeypatch):
    started = {"count": 0}

    def fake_start_worker(_ctx, _spec):
        started["count"] += 1

    monkeypatch.setattr(
        log_stream_service,
        "get_worker_health_snapshot",
        lambda: {"log_stream_fetcher_minecraft": {"running": False}},
    )
    monkeypatch.setattr(log_stream_service, "start_worker", fake_start_worker)

    state = _make_log_state(clients=0)
    state["started"] = True
    ctx = SimpleNamespace(
        LOG_SOURCE_KEYS=("minecraft",),
        log_stream_states={"minecraft": state},
        LOG_FETCHER_IDLE_SLEEP_SECONDS=2,
    )

    log_stream_service.ensure_log_stream_fetcher_started(ctx, "minecraft")

    assert started["count"] == 1
    assert state["started"] is True


def test_start_idle_player_watcher_restarts_when_health_is_not_running(monkeypatch):
    started = {"count": 0}

    def fake_start_worker(_ctx, _spec):
        started["count"] += 1

    monkeypatch.setattr(
        session_watchers,
        "get_worker_health_snapshot",
        lambda: {"idle_player_watcher": {"running": False}},
    )
    monkeypatch.setattr(session_watchers, "start_worker", fake_start_worker)

    ctx = SimpleNamespace(
        IDLE_CHECK_INTERVAL_ACTIVE_SECONDS=5.0,
    )

    session_watchers.start_idle_player_watcher(ctx)

    assert started["count"] == 1


def test_collect_dashboard_metrics_best_effort_ensures_session_watchers(monkeypatch):
    ensured = {"count": 0}
    monkeypatch.setattr(
        metrics_runtime.session_watchers_service,
        "ensure_session_watchers_started",
        lambda _ctx: ensured.__setitem__("count", ensured["count"] + 1),
    )
    monkeypatch.setattr(metrics_runtime, "has_active_flask_app_clients", lambda _ctx: True)
    monkeypatch.setattr(metrics_runtime, "get_observed_state", lambda _ctx: {"service_status_raw": "active", "service_status_display": "Running", "players_online": "0"})
    monkeypatch.setattr(metrics_runtime, "get_slow_metrics", lambda _ctx, _status, active_clients=False: {
        "cpu_per_core": ["1.0"],
        "ram_usage": "1 / 2 GB (50.0%)",
        "cpu_frequency": "3.00 GHz",
        "storage_usage": "1G / 10G (10%)",
        "backups_status": "Idle",
    })
    monkeypatch.setattr(metrics_runtime, "_get_backup_and_stale_counts", lambda _ctx: (0, 0, "/tmp/backups"))
    monkeypatch.setattr(metrics_runtime.maintenance_state_store_service, "get_cleanup_meta", lambda _ctx, scope="backups": {
        "last_run_at": "",
        "rule_version": 0,
        "schedule_version": 0,
        "last_changed_by": "",
    })
    monkeypatch.setattr(metrics_runtime.maintenance_state_store_service, "get_cleanup_missed_run_count", lambda _ctx: 0)
    monkeypatch.setattr(metrics_runtime.maintenance_scheduler_service, "get_next_cleanup_run_at", lambda _ctx, scope="backups": "")

    ctx = SimpleNamespace(
        DISPLAY_TZ=None,
        BACKUP_WARNING_TTL_SECONDS=60,
        log_mcweb_exception=lambda *_args, **_kwargs: None,
        get_status=lambda: "active",
        is_storage_low=lambda _usage: False,
        low_storage_error_message=lambda _usage: "",
        _probe_minecraft_runtime_metrics=lambda force=False: ("0", "12.3ms"),
        get_players_online=lambda: "0",
        get_tick_rate=lambda: "12.3ms",
        get_session_duration_text=lambda: "00:01:00",
        get_backup_schedule_times=lambda _status: {"last_backup_time": "--", "next_backup_time": "--"},
        get_backup_status=lambda: ("Idle", "stat-yellow"),
        get_backup_warning_state=lambda _ttl: {"seq": 0, "message": ""},
        get_service_status_class=lambda _status: "stat-green",
        get_world_name=lambda: "World",
        is_rcon_enabled=lambda: True,
        get_idle_countdown=lambda _status, _players: "00:00",
        re=__import__("re"),
    )

    snapshot = metrics_runtime.collect_dashboard_metrics(ctx)

    assert ensured["count"] == 1
    assert snapshot["service_status"] == "Running"
