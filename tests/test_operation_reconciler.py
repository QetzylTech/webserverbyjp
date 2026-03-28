import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from app.core import state_store as state_store_service
from app.services import dashboard_operations_runtime


class OperationReconcilerTests(unittest.TestCase):
    def _db_path(self, stem):
        return Path("data") / f"{stem}_{uuid.uuid4().hex[:8]}.sqlite3"

    def _ctx(self, db_path, *, service_status="inactive", restore_payload=None, intent=""):
        intent_state = {"value": intent}
        return SimpleNamespace(
            APP_STATE_DB_PATH=Path(db_path),
            OPERATION_RECONCILE_INTERVAL_SECONDS=0.1,
            OPERATION_INTENT_STALE_SECONDS=0.1,
            OPERATION_START_TIMEOUT_SECONDS=0.1,
            OPERATION_STOP_TIMEOUT_SECONDS=0.1,
            OPERATION_RESTORE_TIMEOUT_SECONDS=0.1,
            OFF_STATES={"inactive", "failed"},
            SERVICE="minecraft",
            MINECRAFT_LOGS_DIR=Path("data"),
            operation_reconciler_started=False,
            operation_reconciler_start_lock=threading.Lock(),
            get_status=lambda: service_status,
            get_restore_status=lambda since_seq=0, job_id=None: (restore_payload or {"running": True, "result": None}),
            get_service_status_intent=lambda: intent_state["value"],
            set_service_status_intent=lambda value: intent_state.__setitem__("value", value),
            _intent_state=intent_state,
            log_mcweb_exception=lambda *_args, **_kwargs: None,
        )

    def test_reconcile_start_marks_observed_when_service_active(self):
        db_path = self._db_path("test_ops_reconcile")
        state_store_service.create_operation(
            db_path,
            op_id="start-op-1",
            op_type="start",
            target="minecraft",
            status="in_progress",
            payload={},
        )
        ctx = self._ctx(db_path, service_status="active")
        updated = dashboard_operations_runtime.reconcile_operations_once(ctx)
        self.assertGreaterEqual(updated, 1)
        item = state_store_service.get_operation(db_path, "start-op-1")
        self.assertEqual(item["status"], "observed")

    def test_reconcile_start_marks_failed_on_timeout(self):
        db_path = self._db_path("test_ops_reconcile")
        state_store_service.create_operation(
            db_path,
            op_id="start-op-2",
            op_type="start",
            target="minecraft",
            status="intent",
            payload={},
        )
        ctx = self._ctx(db_path, service_status="inactive")
        with patch("app.services.dashboard_operations_runtime.time.time", side_effect=[9_999_999_999.0, 9_999_999_999.0]):
            updated = dashboard_operations_runtime.reconcile_operations_once(ctx)
        self.assertGreaterEqual(updated, 1)
        item = state_store_service.get_operation(db_path, "start-op-2")
        self.assertEqual(item["status"], "failed")

    def test_reconcile_start_in_progress_fails_when_service_returns_off(self):
        db_path = self._db_path("test_ops_reconcile")
        state_store_service.create_operation(
            db_path,
            op_id="start-op-3",
            op_type="start",
            target="minecraft",
            status="in_progress",
            payload={},
        )
        ctx = self._ctx(db_path, service_status="inactive", intent="starting")
        ctx.OPERATION_START_TIMEOUT_SECONDS = 9_999_999_999.0
        with patch.object(dashboard_operations_runtime.ports.log, "minecraft_startup_probe_output", return_value="[Server thread/INFO]: Bind failed"):
            with patch("app.services.dashboard_operations_runtime.time.time", side_effect=[9_999_999_999.0, 9_999_999_999.0]):
                updated = dashboard_operations_runtime.reconcile_operations_once(ctx)
        self.assertGreaterEqual(updated, 1)
        item = state_store_service.get_operation(db_path, "start-op-3")
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["error_code"], "start_failed")
        self.assertIn("Recent log: [Server thread/INFO]: Bind failed", item["message"])
        self.assertIsNone(ctx._intent_state["value"])

    def test_reconcile_restore_uses_restore_status_result(self):
        db_path = self._db_path("test_ops_reconcile")
        state_store_service.create_operation(
            db_path,
            op_id="restore-op-1",
            op_type="restore",
            target="world.zip",
            status="in_progress",
            payload={"restore_job_id": "restore-job-1"},
        )
        ctx = self._ctx(
            db_path,
            service_status="inactive",
            restore_payload={"running": False, "result": {"ok": True, "message": "done"}},
        )
        updated = dashboard_operations_runtime.reconcile_operations_once(ctx)
        self.assertGreaterEqual(updated, 1)
        item = state_store_service.get_operation(db_path, "restore-op-1")
        self.assertEqual(item["status"], "observed")

    def test_reconcile_stop_marks_observed_and_clears_stale_shutdown_intent(self):
        db_path = self._db_path("test_ops_reconcile")
        state_store_service.create_operation(
            db_path,
            op_id="stop-op-1",
            op_type="stop",
            target="minecraft",
            status="in_progress",
            payload={},
        )
        ctx = self._ctx(db_path, service_status="inactive", intent="shutting")
        updated = dashboard_operations_runtime.reconcile_operations_once(ctx)
        self.assertGreaterEqual(updated, 1)
        item = state_store_service.get_operation(db_path, "stop-op-1")
        self.assertEqual(item["status"], "observed")
        self.assertIsNone(ctx._intent_state["value"])

    def test_reconcile_stop_timeout_clears_stale_shutdown_intent(self):
        db_path = self._db_path("test_ops_reconcile")
        state_store_service.create_operation(
            db_path,
            op_id="stop-op-2",
            op_type="stop",
            target="minecraft",
            status="intent",
            payload={},
        )
        ctx = self._ctx(db_path, service_status="activating", intent="shutting")
        with patch("app.services.dashboard_operations_runtime.time.time", side_effect=[9_999_999_999.0, 9_999_999_999.0]):
            updated = dashboard_operations_runtime.reconcile_operations_once(ctx)
        self.assertGreaterEqual(updated, 1)
        item = state_store_service.get_operation(db_path, "stop-op-2")
        self.assertEqual(item["status"], "failed")
        self.assertIsNone(ctx._intent_state["value"])


if __name__ == "__main__":
    unittest.main()
