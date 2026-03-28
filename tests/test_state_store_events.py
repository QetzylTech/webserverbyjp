import unittest
from pathlib import Path
import uuid

from app.core import state_store as state_store_service


class StateStoreEventsTests(unittest.TestCase):
    def test_append_and_list_events(self):
        db_path = Path("data") / f"test_state_events_{uuid.uuid4().hex[:10]}.sqlite3"
        first_id = state_store_service.append_event(
            db_path,
            topic="metrics_snapshot",
            payload={"snapshot": {"service_status": "Off"}},
        )
        second_id = state_store_service.append_event(
            db_path,
            topic="metrics_snapshot",
            payload={"snapshot": {"service_status": "Running"}},
        )
        self.assertGreater(second_id, first_id)

        rows = state_store_service.list_events_since(
            db_path,
            topic="metrics_snapshot",
            since_id=first_id,
            limit=50,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], second_id)
        self.assertEqual(rows[0]["payload"]["snapshot"]["service_status"], "Running")

    def test_get_latest_event(self):
        db_path = Path("data") / f"test_state_events_{uuid.uuid4().hex[:10]}.sqlite3"
        state_store_service.append_event(db_path, topic="log:minecraft", payload={"line": "a"})
        state_store_service.append_event(db_path, topic="log:minecraft", payload={"line": "b"})
        latest = state_store_service.get_latest_event(db_path, topic="log:minecraft")
        self.assertIsInstance(latest, dict)
        self.assertEqual(latest["payload"]["line"], "b")

    def test_operation_create_and_update_emit_operation_update_events(self):
        db_path = Path("data") / f"test_operation_events_{uuid.uuid4().hex[:10]}.sqlite3"
        state_store_service.create_operation(
            db_path,
            op_id="start-1",
            op_type="start",
            status="intent",
            checkpoint="created",
        )
        created = state_store_service.get_latest_event(db_path, topic="operation_update")
        self.assertIsInstance(created, dict)
        self.assertEqual(created["payload"]["operation"]["op_id"], "start-1")
        self.assertEqual(created["payload"]["operation"]["status"], "intent")

        first_id = int(created["id"])
        state_store_service.update_operation(
            db_path,
            op_id="start-1",
            status="observed",
            checkpoint="observed",
            finished=True,
        )
        rows = state_store_service.list_events_since(
            db_path,
            topic="operation_update",
            since_id=first_id,
            limit=10,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload"]["operation"]["op_id"], "start-1")
        self.assertEqual(rows[0]["payload"]["operation"]["status"], "observed")


if __name__ == "__main__":
    unittest.main()
