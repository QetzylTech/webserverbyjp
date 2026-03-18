import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services import maintenance_engine
from app.services.maintenance_policy import _cleanup_validate_rules
from app.services.maintenance_state_store import _cleanup_default_config, _cleanup_migrate_config_dict, get_cleanup_missed_run_count


class MaintenanceAgeGuardTests(unittest.TestCase):
    def test_age_minimum_three_in_validation(self):
        ok, rules = _cleanup_validate_rules({"age": {"days": 1}})
        self.assertTrue(ok)
        self.assertEqual(rules["age"]["days"], 3)

    def test_age_minimum_three_in_migration(self):
        default_cfg = _cleanup_default_config()
        loaded = {"rules": {"age": {"days": 1}}}
        ctx = {
            "MAINTENANCE_SCOPE_BACKUP_ZIP": True,
            "MAINTENANCE_SCOPE_STALE_WORLD_DIR": True,
            "MAINTENANCE_SCOPE_OLD_WORLD_ZIP": True,
            "MAINTENANCE_GUARD_NEVER_DELETE_NEWEST_N": 1,
            "MAINTENANCE_GUARD_NEVER_DELETE_LAST_BACKUP": True,
            "MAINTENANCE_GUARD_PROTECT_ACTIVE_WORLD": True,
        }
        cfg = _cleanup_migrate_config_dict(ctx, loaded, default_cfg)
        self.assertEqual(cfg["rules"]["age"]["days"], 3)

    def test_missed_run_count_returns_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session_file = root / "data" / "session.txt"
            session_file.parent.mkdir(parents=True, exist_ok=True)
            session_file.write_text("", encoding="utf-8")
            payload = {"missed_runs": [{"at": "t1"}, {"at": "t2"}, {"at": "t3"}]}
            (session_file.parent / "cleanup_non_normal.txt").write_text(json.dumps(payload), encoding="utf-8")

            ctx = {"session_state": SimpleNamespace(session_file=str(session_file))}
            count = get_cleanup_missed_run_count(ctx)
            self.assertEqual(count, 3)


class MaintenanceDeterminismTests(unittest.TestCase):
    def test_cleanup_selection_is_deterministic(self):
        cfg = _cleanup_default_config()
        cfg["rules"]["age"]["enabled"] = True
        cfg["rules"]["age"]["days"] = 3
        cfg["rules"]["count"]["enabled"] = False
        cfg["rules"]["space"]["enabled"] = False
        cfg["rules"]["guards"]["never_delete_newest_n_per_category"] = 0
        cfg["rules"]["guards"]["never_delete_last_backup_overall"] = False

        now = 1_000_000.0
        candidates = [
            {
                "category": "stale_world_dir",
                "path": "/data/old_world_1",
                "name": "old_world_1",
                "is_dir": True,
                "size": 100,
                "mtime": now - (5 * 86400),
                "eligible": True,
                "reasons": [],
            },
            {
                "category": "stale_world_dir",
                "path": "/data/old_world_2",
                "name": "old_world_2",
                "is_dir": True,
                "size": 100,
                "mtime": now - (2 * 86400),
                "eligible": True,
                "reasons": [],
            },
            {
                "category": "stale_world_dir",
                "path": "/data/old_world_3",
                "name": "old_world_3",
                "is_dir": True,
                "size": 100,
                "mtime": now - (1 * 86400),
                "eligible": True,
                "reasons": [],
            },
        ]
        shuffled = list(reversed(candidates))

        def _selected_paths(result):
            return {row["path"] for row in result.get("items", []) if row.get("selected_for_delete")}

        ctx = SimpleNamespace(BACKUP_DIR="/tmp")
        with patch.object(maintenance_engine, "_cleanup_collect_candidates", return_value=candidates), patch(
            "app.services.maintenance_rules.time.time", return_value=now
        ), patch("app.services.maintenance_rules._cleanup_safe_used_percent", return_value=(None, None, None)):
            result_a = maintenance_engine._cleanup_evaluate(ctx, cfg, mode="rule", apply_changes=False)

        with patch.object(maintenance_engine, "_cleanup_collect_candidates", return_value=shuffled), patch(
            "app.services.maintenance_rules.time.time", return_value=now
        ), patch("app.services.maintenance_rules._cleanup_safe_used_percent", return_value=(None, None, None)):
            result_b = maintenance_engine._cleanup_evaluate(ctx, cfg, mode="rule", apply_changes=False)

        self.assertEqual(_selected_paths(result_a), _selected_paths(result_b))
        self.assertEqual(_selected_paths(result_a), {"/data/old_world_1"})


if __name__ == "__main__":
    unittest.main()
