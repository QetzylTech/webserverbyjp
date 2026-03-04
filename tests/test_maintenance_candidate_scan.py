import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.maintenance_candidate_scan import _cleanup_collect_candidates
from app.services.maintenance_state_store import _cleanup_default_config


class MaintenanceCandidateScanTests(unittest.TestCase):
    def test_collect_candidates_includes_backup_zip_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "world_manual.zip").write_bytes(b"zip-data")

            session_file = root / "data" / "session.txt"
            session_file.parent.mkdir(parents=True, exist_ok=True)
            session_file.write_text("", encoding="utf-8")

            server_props = root / "server.properties"
            server_props.write_text("level-name=world\n", encoding="utf-8")
            world_dir = root / "world"
            world_dir.mkdir(parents=True, exist_ok=True)

            state = {
                "BACKUP_DIR": backup_dir,
                "session_state": SimpleNamespace(session_file=str(session_file)),
                "SERVER_PROPERTIES_CANDIDATES": [server_props],
            }
            cfg = _cleanup_default_config()
            cfg["rules"]["categories"]["backup_zip"] = True

            items = _cleanup_collect_candidates(state, cfg)
            self.assertIsInstance(items, list)
            backup_items = [row for row in items if row.get("category") == "backup_zip"]
            self.assertEqual(len(backup_items), 1)
            self.assertEqual(backup_items[0].get("name"), "world_manual.zip")

    def test_collect_candidates_includes_old_world_entries_from_unified_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)

            session_file = root / "data" / "session.txt"
            old_worlds_dir = session_file.parent / "old_worlds"
            old_world_dir = old_worlds_dir / "world_2026-01-01_00-00-00"
            nested_dir = old_world_dir / "nested"
            nested_zip = nested_dir / "nested.zip"
            old_worlds_dir.mkdir(parents=True, exist_ok=True)
            nested_dir.mkdir(parents=True, exist_ok=True)
            (old_world_dir / "level.dat").write_text("abc", encoding="utf-8")
            nested_zip.write_bytes(b"zip-data")
            (old_worlds_dir / "flat.zip").write_bytes(b"zip-flat")
            session_file.write_text("", encoding="utf-8")

            server_props = root / "server.properties"
            server_props.write_text("level-name=world\n", encoding="utf-8")
            world_dir = root / "world"
            world_dir.mkdir(parents=True, exist_ok=True)

            state = {
                "BACKUP_DIR": backup_dir,
                "session_state": SimpleNamespace(session_file=str(session_file)),
                "SERVER_PROPERTIES_CANDIDATES": [server_props],
            }
            cfg = _cleanup_default_config()
            cfg["rules"]["categories"]["stale_world_dir"] = True
            cfg["rules"]["categories"]["old_world_zip"] = True

            items = _cleanup_collect_candidates(state, cfg)
            stale_dirs = [row for row in items if row.get("category") == "stale_world_dir"]
            old_zips = [row for row in items if row.get("category") == "old_world_zip"]
            stale_names = {row.get("name") for row in stale_dirs}
            zip_names = {row.get("name") for row in old_zips}

            self.assertIn("world_2026-01-01_00-00-00", stale_names)
            self.assertIn("flat.zip", zip_names)
            self.assertIn("nested.zip", zip_names)


if __name__ == "__main__":
    unittest.main()
