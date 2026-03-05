import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from flask import Flask

from app.routes import dashboard_control_routes


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._target = target
        self.daemon = daemon

    def start(self):
        if self._target:
            self._target()


class ControlRouteStartSemanticsTests(unittest.TestCase):
    def _build_state(self, events, *, start_ok=True, session_ok=True, db_path=None):
        return {
            "is_storage_low": lambda: False,
            "low_storage_error_message": lambda: "low",
            "log_mcweb_action": lambda action, **kwargs: events.append(("log", action, kwargs)),
            "_low_storage_blocked_response": lambda message: (message, 409),
            "set_service_status_intent": lambda intent: events.append(("intent", intent)),
            "invalidate_status_cache": lambda: events.append(("invalidate",)),
            "write_session_start_time": lambda: (events.append(("write_session",)) or (1.0 if session_ok else None)),
            "_session_write_failed_response": lambda: ("session failed", 500),
            "reset_backup_schedule_state": lambda: events.append(("reset_schedule",)),
            "start_service_non_blocking": lambda timeout=12: {"ok": bool(start_ok), "message": "start failed"},
            "log_mcweb_exception": lambda *_args, **_kwargs: None,
            "_start_failed_response": lambda message: (message, 500),
            "_ok_response": lambda: ("ok", 200),
            "validate_sudo_password": lambda password: password == "ok",
            "_password_rejected_response": lambda: ("password incorrect", 403),
            "record_successful_password_ip": lambda: None,
            "graceful_stop_minecraft": lambda: {"systemd_ok": True, "backup_ok": True},
            "clear_session_start_time": lambda: None,
            "run_backup_script": lambda trigger="manual": True,
            "backup_state": SimpleNamespace(lock=SimpleNamespace(__enter__=lambda s: None, __exit__=lambda s, *a: None), last_error=""),
            "_backup_failed_response": lambda message: (message, 500),
            "start_restore_job": lambda filename: {"ok": True, "job_id": "j1"},
            "get_restore_status": lambda since_seq="0", job_id=None: {"ok": True, "running": False, "events": []},
            "_rcon_rejected_response": lambda message, status=400: (message, status),
            "is_rcon_enabled": lambda: True,
            "get_status": lambda: "active",
            "_run_mcrcon": lambda command, timeout=8: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
            "APP_STATE_DB_PATH": Path(db_path or f"data/test_state_{uuid.uuid4().hex}.sqlite3"),
        }

    def test_start_writes_session_only_after_successful_start_command(self):
        app = Flask(__name__)
        events = []
        state = self._build_state(events, start_ok=True, session_ok=True)
        with patch.object(dashboard_control_routes.threading, "Thread", _ImmediateThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            response = app.test_client().post("/start")
        self.assertEqual(response.status_code, 202)
        start_idx = next(i for i, e in enumerate(events) if e[0] == "write_session")
        self.assertIn(("reset_schedule",), events)
        self.assertGreaterEqual(start_idx, 0)

    def test_start_does_not_write_session_when_start_command_fails(self):
        app = Flask(__name__)
        events = []
        state = self._build_state(events, start_ok=False, session_ok=True)
        with patch.object(dashboard_control_routes.threading, "Thread", _ImmediateThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            response = app.test_client().post("/start")
        self.assertEqual(response.status_code, 202)
        self.assertFalse(any(event[0] == "write_session" for event in events))


if __name__ == "__main__":
    unittest.main()
