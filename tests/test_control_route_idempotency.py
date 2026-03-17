import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid
import threading

from flask import Flask

from app.core import state_store as state_store_service
from app.routes import dashboard_control_routes


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._target = target
        self.daemon = daemon

    def start(self):
        if self._target:
            self._target()


class _NoopThread:
    def __init__(self, target=None, daemon=None):
        self._target = target
        self.daemon = daemon

    def start(self):
        return None


class ControlRouteIdempotencyTests(unittest.TestCase):
    def _db_path(self, stem):
        return Path("data") / f"{stem}_{uuid.uuid4().hex[:8]}.sqlite3"

    def _base_state(self, db_path, *, start_results=None, stop_results=None, backup_results=None, restore_results=None):
        start_queue = list(start_results or [{"ok": True}])
        stop_queue = list(stop_results or [{"systemd_ok": True, "backup_ok": True}])
        backup_queue = list(backup_results or [True])
        restore_queue = list(restore_results or [{"ok": True, "job_id": "job-1"}])

        def _next_start(timeout=12):
            if len(start_queue) > 1:
                return start_queue.pop(0)
            return start_queue[0]

        def _next_restore(_filename):
            if len(restore_queue) > 1:
                return restore_queue.pop(0)
            return restore_queue[0]

        def _next_backup(trigger="manual"):
            if len(backup_queue) > 1:
                return bool(backup_queue.pop(0))
            return bool(backup_queue[0])

        def _next_stop():
            if len(stop_queue) > 1:
                return stop_queue.pop(0)
            return stop_queue[0]

        return {
            "is_storage_low": lambda: False,
            "low_storage_error_message": lambda: "low",
            "log_mcweb_action": lambda *_args, **_kwargs: None,
            "_low_storage_blocked_response": lambda message: (message, 409),
            "set_service_status_intent": lambda *_args, **_kwargs: None,
            "invalidate_status_cache": lambda: None,
            "write_session_start_time": lambda: 1.0,
            "reset_backup_schedule_state": lambda: None,
            "start_service_non_blocking": _next_start,
            "log_mcweb_exception": lambda *_args, **_kwargs: None,
            "_start_failed_response": lambda message: (message, 500),
            "_ok_response": lambda: ("ok", 200),
            "validate_sudo_password": lambda password: password == "ok",
            "_password_rejected_response": lambda: ("password incorrect", 403),
            "record_successful_password_ip": lambda: None,
            "graceful_stop_minecraft": _next_stop,
            "clear_session_start_time": lambda: None,
            "run_backup_script": _next_backup,
            "backup_state": SimpleNamespace(lock=threading.Lock(), last_error=""),
            "_backup_failed_response": lambda message: (message, 500),
            "start_restore_job": _next_restore,
            "get_restore_status": lambda since_seq="0", job_id=None: {"ok": True, "running": False, "result": {"ok": True, "message": "done"}},
            "_rcon_rejected_response": lambda message, status=400: (message, status),
            "is_rcon_enabled": lambda: True,
            "get_status": lambda: "active",
            "_run_mcrcon": lambda command, timeout=8: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
            "APP_STATE_DB_PATH": Path(db_path),
            "SERVICE": "minecraft",
        }

    def test_web_role_can_execute_start_locally(self):
        db_path = self._db_path("test_web_role_enqueue_start")
        app = Flask(__name__)
        state = self._base_state(db_path)
        state["PROCESS_ROLE"] = "web"
        started = {"count": 0}

        class _CountStartedThread:
            def __init__(self, target=None, daemon=None):
                self._target = target
                self.daemon = daemon

            def start(self):
                started["count"] += 1
                if self._target:
                    self._target()

        with patch.object(dashboard_control_routes.threading, "Thread", _CountStartedThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            client = app.test_client()
            resp = client.post("/start")
        body = resp.get_json() or {}
        self.assertEqual(resp.status_code, 202)
        self.assertGreaterEqual(started["count"], 1)
        op = state_store_service.get_operation(db_path, body.get("op_id", ""))
        self.assertIn(op.get("status"), {"in_progress", "observed"})

    def test_start_idempotency_reuses_existing_operation(self):
        db_path = self._db_path("test_start_idempotency_reuse")
        app = Flask(__name__)
        state = self._base_state(db_path)
        with patch.object(dashboard_control_routes.threading, "Thread", _ImmediateThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            client = app.test_client()
            first = client.post("/start", headers={"X-Idempotency-Key": "start-key-1"})
            second = client.post("/start", headers={"X-Idempotency-Key": "start-key-1"})

        first_body = first.get_json() or {}
        second_body = second.get_json() or {}
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first_body.get("op_id"), second_body.get("op_id"))
        self.assertTrue(second_body.get("existing"))
        self.assertFalse(second_body.get("resumed"))

    def test_start_idempotency_resumes_failed_operation(self):
        db_path = self._db_path("test_start_idempotency_resume")
        app = Flask(__name__)
        state = self._base_state(db_path, start_results=[{"ok": False, "message": "failed"}, {"ok": True}])
        with patch.object(dashboard_control_routes.threading, "Thread", _ImmediateThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            client = app.test_client()
            first = client.post("/start", headers={"X-Idempotency-Key": "start-key-2"})
            op_id = (first.get_json() or {}).get("op_id", "")
            before = state_store_service.get_operation(db_path, op_id)
            self.assertEqual(before["status"], "failed")

            second = client.post("/start", headers={"X-Idempotency-Key": "start-key-2"})
            after = state_store_service.get_operation(db_path, op_id)

        second_body = second.get_json() or {}
        self.assertEqual(second.status_code, 202)
        self.assertTrue(second_body.get("existing"))
        self.assertTrue(second_body.get("resumed"))
        self.assertEqual(second_body.get("op_id"), op_id)
        self.assertEqual(after["status"], "in_progress")
        self.assertEqual(after["attempt"], 2)

    def test_restore_idempotency_conflict_different_target(self):
        db_path = self._db_path("test_restore_idempotency_conflict")
        state_store_service.create_operation(
            db_path,
            op_id="restore-existing",
            op_type="restore",
            target="a.zip",
            idempotency_key="restore-key-1",
            status="observed",
            checkpoint="observed",
            payload={},
        )
        app = Flask(__name__)
        state = self._base_state(db_path)
        dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
        client = app.test_client()
        response = client.post(
            "/restore-backup",
            data={"sudo_password": "ok", "filename": "b.zip"},
            headers={"X-Idempotency-Key": "restore-key-1"},
        )
        body = response.get_json() or {}
        self.assertEqual(response.status_code, 409)
        self.assertEqual(body.get("error"), "idempotency_key_conflict")

    def test_restore_idempotency_resumes_failed_operation(self):
        db_path = self._db_path("test_restore_idempotency_resume")
        app = Flask(__name__)
        state = self._base_state(
            db_path,
            restore_results=[
                {"ok": False, "error": "restore_failed", "message": "failed"},
                {"ok": True, "job_id": "job-2"},
            ],
        )
        with patch.object(dashboard_control_routes.threading, "Thread", _ImmediateThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            client = app.test_client()
            first = client.post(
                "/restore-backup",
                data={"sudo_password": "ok", "filename": "a.zip"},
                headers={"X-Idempotency-Key": "restore-key-2"},
            )
            op_id = (first.get_json() or {}).get("op_id", "")
            before = state_store_service.get_operation(db_path, op_id)
            self.assertEqual(before["status"], "failed")

            second = client.post(
                "/restore-backup",
                data={"sudo_password": "ok", "filename": "a.zip"},
                headers={"X-Idempotency-Key": "restore-key-2"},
            )
            after = state_store_service.get_operation(db_path, op_id)

        second_body = second.get_json() or {}
        self.assertEqual(second.status_code, 202)
        self.assertTrue(second_body.get("existing"))
        self.assertTrue(second_body.get("resumed"))
        self.assertEqual(second_body.get("op_id"), op_id)
        self.assertEqual(after["status"], "observed")
        self.assertEqual(after["attempt"], 2)

    def test_stop_idempotency_reuses_existing_operation(self):
        db_path = self._db_path("test_stop_idempotency_reuse")
        app = Flask(__name__)
        state = self._base_state(db_path)
        with patch.object(dashboard_control_routes.threading, "Thread", _ImmediateThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            client = app.test_client()
            first = client.post("/stop", data={"sudo_password": "ok"}, headers={"X-Idempotency-Key": "stop-key-1"})
            second = client.post("/stop", data={"sudo_password": "ok"}, headers={"X-Idempotency-Key": "stop-key-1"})

        first_body = first.get_json() or {}
        second_body = second.get_json() or {}
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first_body.get("op_id"), second_body.get("op_id"))
        self.assertTrue(second_body.get("existing"))
        self.assertFalse(second_body.get("resumed"))

    def test_stop_idempotency_resumes_failed_operation(self):
        db_path = self._db_path("test_stop_idempotency_resume")
        app = Flask(__name__)
        state = self._base_state(
            db_path,
            stop_results=[
                {"systemd_ok": False, "backup_ok": True},
                {"systemd_ok": True, "backup_ok": True},
            ],
        )
        with patch.object(dashboard_control_routes.threading, "Thread", _ImmediateThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            client = app.test_client()
            first = client.post("/stop", data={"sudo_password": "ok"}, headers={"X-Idempotency-Key": "stop-key-2"})
            op_id = (first.get_json() or {}).get("op_id", "")
            before = state_store_service.get_operation(db_path, op_id)
            self.assertEqual(before["status"], "failed")

            second = client.post("/stop", data={"sudo_password": "ok"}, headers={"X-Idempotency-Key": "stop-key-2"})
            after = state_store_service.get_operation(db_path, op_id)

        second_body = second.get_json() or {}
        self.assertEqual(second.status_code, 202)
        self.assertTrue(second_body.get("existing"))
        self.assertTrue(second_body.get("resumed"))
        self.assertEqual(second_body.get("op_id"), op_id)
        self.assertEqual(after["status"], "observed")
        self.assertEqual(after["attempt"], 2)

    def test_backup_idempotency_reuses_existing_operation(self):
        db_path = self._db_path("test_backup_idempotency_reuse")
        app = Flask(__name__)
        state = self._base_state(db_path)
        with patch.object(dashboard_control_routes.threading, "Thread", _ImmediateThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            client = app.test_client()
            first = client.post("/backup", headers={"X-Idempotency-Key": "backup-key-1"})
            second = client.post("/backup", headers={"X-Idempotency-Key": "backup-key-1"})

        first_body = first.get_json() or {}
        second_body = second.get_json() or {}
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first_body.get("op_id"), second_body.get("op_id"))
        self.assertTrue(second_body.get("existing"))
        self.assertFalse(second_body.get("resumed"))

    def test_backup_idempotency_resumes_failed_operation(self):
        db_path = self._db_path("test_backup_idempotency_resume")
        app = Flask(__name__)
        state = self._base_state(
            db_path,
            backup_results=[False, True],
        )
        with patch.object(dashboard_control_routes.threading, "Thread", _ImmediateThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            client = app.test_client()
            first = client.post("/backup", headers={"X-Idempotency-Key": "backup-key-2"})
            op_id = (first.get_json() or {}).get("op_id", "")
            before = state_store_service.get_operation(db_path, op_id)
            self.assertEqual(before["status"], "failed")

            second = client.post("/backup", headers={"X-Idempotency-Key": "backup-key-2"})
            after = state_store_service.get_operation(db_path, op_id)

        second_body = second.get_json() or {}
        self.assertEqual(second.status_code, 202)
        self.assertTrue(second_body.get("existing"))
        self.assertTrue(second_body.get("resumed"))
        self.assertEqual(second_body.get("op_id"), op_id)
        self.assertEqual(after["status"], "observed")
        self.assertEqual(after["attempt"], 2)

    def test_backup_dedupes_in_flight_without_idempotency_key(self):
        db_path = self._db_path("test_backup_dedupe_without_idempotency")
        app = Flask(__name__)
        state = self._base_state(db_path)
        with patch.object(dashboard_control_routes.threading, "Thread", _NoopThread):
            dashboard_control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_a, **_k: None)
            client = app.test_client()
            first = client.post("/backup")
            second = client.post("/backup")

        first_body = first.get_json() or {}
        second_body = second.get_json() or {}
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first_body.get("op_id"), second_body.get("op_id"))
        self.assertTrue(second_body.get("existing"))
        self.assertFalse(second_body.get("resumed"))


if __name__ == "__main__":
    unittest.main()
