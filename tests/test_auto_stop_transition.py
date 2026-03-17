import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import session_watchers, stop_usecase


class AutoStopTransitionTests(unittest.TestCase):
    def test_stop_server_automatically_publishes_shutdown_transition_and_completion(self):
        events = []
        ctx = SimpleNamespace(
            invalidate_status_cache=lambda: events.append("invalidate"),
            _collect_and_publish_metrics=lambda: events.append("publish"),
        )

        with patch.object(stop_usecase, "set_service_status_intent", side_effect=lambda _ctx, intent: events.append(("intent", intent))), \
             patch.object(stop_usecase, "graceful_stop_minecraft", side_effect=lambda _ctx, trigger="session_end": events.append(("graceful_stop", trigger)) or {"systemd_ok": True, "backup_ok": True}), \
             patch.object(stop_usecase, "clear_session_start_time", side_effect=lambda _ctx: events.append("clear_session")), \
             patch.object(stop_usecase, "reset_backup_schedule_state", side_effect=lambda _ctx: events.append("reset_backup")):
            result = stop_usecase.stop_server_automatically(ctx, trigger="session_end")

        self.assertEqual(result, {"systemd_ok": True, "backup_ok": True})
        self.assertEqual(events.count("publish"), 2)
        self.assertEqual(events[0], ("intent", "shutting"))
        self.assertIn(("graceful_stop", "session_end"), events)

    def test_stop_server_automatically_preserves_session_state_when_backup_fails(self):
        events = []
        ctx = SimpleNamespace(
            invalidate_status_cache=lambda: events.append("invalidate"),
            _collect_and_publish_metrics=lambda: events.append("publish"),
        )

        with patch.object(stop_usecase, "set_service_status_intent", side_effect=lambda _ctx, intent: events.append(("intent", intent))), \
             patch.object(stop_usecase, "graceful_stop_minecraft", return_value={"systemd_ok": True, "backup_ok": False}), \
             patch.object(stop_usecase, "clear_session_start_time", side_effect=lambda _ctx: events.append("clear_session")), \
             patch.object(stop_usecase, "reset_backup_schedule_state", side_effect=lambda _ctx: events.append("reset_backup")):
            result = stop_usecase.stop_server_automatically(ctx, trigger="session_end")

        self.assertEqual(result, {"systemd_ok": True, "backup_ok": False})
        self.assertNotIn("clear_session", events)
        self.assertNotIn("reset_backup", events)
        self.assertEqual(events.count("publish"), 2)

    def test_backup_session_watcher_keeps_session_state_when_shutdown_backup_fails(self):
        backup_state = SimpleNamespace(lock=threading.Lock(), periodic_runs=4)
        events = []
        ctx = SimpleNamespace(
            backup_state=backup_state,
            BACKUP_INTERVAL_SECONDS=300,
            BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS=5,
            BACKUP_WATCH_INTERVAL_OFF_SECONDS=15,
            read_session_start_time=lambda: 100.0,
            clear_session_start_time=lambda: events.append("clear_session"),
            run_backup_script=lambda count_skip_as_success=True, trigger="manual": events.append((trigger, count_skip_as_success)) or False,
            get_status=lambda: "inactive",
            log_mcweb_exception=lambda *_args, **_kwargs: None,
        )

        with patch("app.services.session_watchers.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                session_watchers.backup_session_watcher(ctx)

        self.assertEqual(events, [("session_end", False)])
        self.assertEqual(backup_state.periodic_runs, 4)

    def test_backup_session_watcher_clears_session_state_after_shutdown_backup_succeeds(self):
        backup_state = SimpleNamespace(lock=threading.Lock(), periodic_runs=4)
        events = []
        ctx = SimpleNamespace(
            backup_state=backup_state,
            BACKUP_INTERVAL_SECONDS=300,
            BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS=5,
            BACKUP_WATCH_INTERVAL_OFF_SECONDS=15,
            read_session_start_time=lambda: 100.0,
            clear_session_start_time=lambda: events.append("clear_session"),
            run_backup_script=lambda count_skip_as_success=True, trigger="manual": events.append((trigger, count_skip_as_success)) or True,
            get_status=lambda: "inactive",
            log_mcweb_exception=lambda *_args, **_kwargs: None,
        )

        with patch("app.services.session_watchers.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                session_watchers.backup_session_watcher(ctx)

        self.assertEqual(events, [("session_end", False), "clear_session"])
        self.assertEqual(backup_state.periodic_runs, 0)
    def test_idle_watcher_keeps_countdown_pinned_at_zero_after_auto_stop_fires(self):
        events = []
        ctx = SimpleNamespace(
            IDLE_ZERO_PLAYERS_SECONDS=300,
            IDLE_CHECK_INTERVAL_ACTIVE_SECONDS=5,
            IDLE_CHECK_INTERVAL_OFF_SECONDS=15,
            idle_zero_players_since=100.0,
            idle_lock=threading.Lock(),
            get_status=lambda: "active",
            get_players_online=lambda: "0",
            get_service_status_intent=lambda: "",
            stop_server_automatically=lambda: events.append("auto_stop"),
            log_mcweb_exception=lambda *_args, **_kwargs: None,
        )

        with patch("app.services.session_watchers.time.time", side_effect=[401.0]), \
             patch("app.services.session_watchers.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                session_watchers.idle_player_watcher(ctx)

        self.assertEqual(events, ["auto_stop"])
        self.assertEqual(ctx.idle_zero_players_since, 101.0)


if __name__ == "__main__":
    unittest.main()
