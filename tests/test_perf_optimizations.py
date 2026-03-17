import tempfile
import unittest
from pathlib import Path
import uuid
from unittest.mock import patch

from app.core import state_store as state_store_service
from app.services import dashboard_state_runtime as runtime_service
from app.services import maintenance_engine as maintenance_engine_service
from app.services import maintenance_state_store as maintenance_store_service


class PerformanceOptimizationTests(unittest.TestCase):
    def setUp(self):
        runtime_service._OBSERVED_OPS_CACHE.update(
            {
                "db_path": "",
                "cached_at": 0.0,
                "latest_start": None,
                "latest_stop": None,
                "latest_restore": None,
            }
        )
        runtime_service.invalidate_observed_state_cache(None)
        maintenance_store_service._CLEANUP_CONFIG_CACHE.clear()

    def test_observed_state_operation_aggregation_uses_short_ttl_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {"count": 0}

            def _latest(_db_path, op_type):
                calls["count"] += 1
                if op_type == "start":
                    return {"status": "intent"}
                return {"status": "observed"}

            class Ctx:
                APP_STATE_DB_PATH = root / "state.sqlite3"
                WORLD_DIR = root / "world"
                BACKUP_DIR = root / "backups"
                AUTO_SNAPSHOT_DIR = root / "backups" / "snapshots"
                OFF_STATES = {"inactive", "failed"}

                @staticmethod
                def get_status():
                    return "inactive"

                @staticmethod
                def get_players_online():
                    return "0"

                @staticmethod
                def get_service_status_display(status, _players):
                    return str(status)

                @staticmethod
                def get_service_status_class(_status):
                    return "stat-green"

            with patch.object(runtime_service.state_store_service, "get_latest_operation_for_type", side_effect=_latest):
                first = runtime_service.get_observed_state(Ctx())
                second = runtime_service.get_observed_state(Ctx())

            self.assertIsInstance(first, dict)
            self.assertIsInstance(second, dict)
            self.assertEqual(calls["count"], 3)

    def test_observed_state_returns_payload_when_no_active_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            class Ctx:
                APP_STATE_DB_PATH = root / "state.sqlite3"
                WORLD_DIR = root / "world"
                BACKUP_DIR = root / "backups"
                AUTO_SNAPSHOT_DIR = root / "backups" / "snapshots"
                OFF_STATES = {"inactive", "failed"}

                @staticmethod
                def get_status():
                    return "inactive"

                @staticmethod
                def get_players_online():
                    return "0"

                @staticmethod
                def get_service_status_display(status, _players):
                    return str(status)

                @staticmethod
                def get_service_status_class(_status):
                    return "stat-green"

            with patch.object(
                runtime_service.state_store_service,
                "get_latest_operation_for_type",
                return_value={"status": "observed"},
            ):
                observed = runtime_service.get_observed_state(Ctx())
            self.assertIsInstance(observed, dict)
            self.assertEqual(observed.get("service_status_raw"), "inactive")

    def test_observed_state_keeps_active_when_start_intent_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            class Ctx:
                APP_STATE_DB_PATH = root / "state.sqlite3"
                WORLD_DIR = root / "world"
                BACKUP_DIR = root / "backups"
                AUTO_SNAPSHOT_DIR = root / "backups" / "snapshots"
                OFF_STATES = {"inactive", "failed"}

                @staticmethod
                def get_status():
                    return "active"

                @staticmethod
                def get_players_online():
                    return "0"

                @staticmethod
                def get_service_status_display(status, _players):
                    return str(status)

                @staticmethod
                def get_service_status_class(_status):
                    return "stat-green"

            def _latest(_db_path, op_type):
                if op_type == "start":
                    return {"status": "in_progress"}
                return {"status": "observed"}

            runtime_service.invalidate_observed_state_cache(None)
            with patch.object(runtime_service.state_store_service, "get_latest_operation_for_type", side_effect=_latest):
                observed = runtime_service.get_observed_state(Ctx())

            self.assertEqual(observed.get("service_status_raw"), "active")

    def test_observed_state_uses_transition_intent_when_operation_cache_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            class Ctx:
                APP_STATE_DB_PATH = root / "state.sqlite3"
                WORLD_DIR = root / "world"
                BACKUP_DIR = root / "backups"
                AUTO_SNAPSHOT_DIR = root / "backups" / "snapshots"
                OFF_STATES = {"inactive", "failed"}

                @staticmethod
                def get_status():
                    return "inactive"

                @staticmethod
                def get_service_status_intent():
                    return "starting"

                @staticmethod
                def get_players_online():
                    return "0"

                @staticmethod
                def get_service_status_display(status, _players):
                    return str(status)

                @staticmethod
                def get_service_status_class(_status):
                    return "stat-green"

            runtime_service.invalidate_observed_state_cache(None)
            with patch.object(
                runtime_service.state_store_service,
                "get_latest_operation_for_type",
                return_value={"status": "observed"},
            ):
                observed = runtime_service.get_observed_state(Ctx())

            self.assertEqual(observed.get("service_status_raw"), "starting")

    def test_cleanup_load_config_cache_and_save_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {
                "APP_STATE_DB_PATH": root / "state.sqlite3",
                "MAINTENANCE_SCOPE_BACKUP_ZIP": True,
                "MAINTENANCE_SCOPE_STALE_WORLD_DIR": False,
                "MAINTENANCE_SCOPE_OLD_WORLD_ZIP": False,
                "MAINTENANCE_GUARD_NEVER_DELETE_NEWEST_N": 1,
                "MAINTENANCE_GUARD_NEVER_DELETE_LAST_BACKUP": True,
                "MAINTENANCE_GUARD_PROTECT_ACTIVE_WORLD": True,
            }
            load_calls = {"count": 0}

            def _load(_db_path):
                load_calls["count"] += 1
                return {"rules": {"age": {"days": 9}}, "meta": {}, "schedules": [], "scopes": {}}

            with patch.object(maintenance_store_service.state_store_service, "load_cleanup_config", side_effect=_load), patch.object(
                maintenance_store_service.state_store_service, "save_cleanup_config", return_value=None
            ):
                cfg1 = maintenance_store_service._cleanup_load_config(state)
                cfg2 = maintenance_store_service._cleanup_load_config(state)
                self.assertEqual(load_calls["count"], 1)
                self.assertEqual(cfg1.get("rules", {}).get("age", {}).get("days"), 9)
                self.assertEqual(cfg2.get("rules", {}).get("age", {}).get("days"), 9)

                maintenance_store_service._cleanup_save_config(state, cfg1)
                cfg3 = maintenance_store_service._cleanup_load_config(state)
                self.assertEqual(load_calls["count"], 2)
                self.assertEqual(cfg3.get("rules", {}).get("age", {}).get("days"), 9)

    def test_cleanup_evaluate_handles_invalid_candidate_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {"BACKUP_DIR": root}
            cfg = maintenance_store_service._cleanup_default_config()
            with patch.object(maintenance_engine_service, "_cleanup_collect_candidates", return_value=None):
                result = maintenance_engine_service._cleanup_evaluate(state, cfg, mode="rule", apply_changes=False, trigger="preview")
            self.assertTrue(result.get("ok"))
            self.assertEqual(result.get("eligible_count"), 0)
            self.assertEqual(result.get("items"), [])

    def test_operation_batch_update_applies_in_one_call(self):
        db_path = Path("data") / f"test_state_batch_{uuid.uuid4().hex[:10]}.sqlite3"
        state_store_service.create_operation(
            db_path,
            op_id="op-a",
            op_type="backup",
            status="intent",
            checkpoint="intent_created",
            payload={},
        )
        state_store_service.create_operation(
            db_path,
            op_id="op-b",
            op_type="start",
            status="intent",
            checkpoint="intent_created",
            payload={},
        )
        rows = state_store_service.update_operations_batch(
            db_path,
            updates=[
                {"op_id": "op-a", "status": "failed", "error_code": "x", "finished": True, "checkpoint": "failed"},
                {"op_id": "op-b", "status": "observed", "finished": True, "checkpoint": "observed"},
            ],
        )
        self.assertEqual(len(rows), 2)
        op_a = state_store_service.get_operation(db_path, "op-a")
        op_b = state_store_service.get_operation(db_path, "op-b")
        self.assertEqual(op_a.get("status"), "failed")
        self.assertEqual(op_b.get("status"), "observed")


if __name__ == "__main__":
    unittest.main()
