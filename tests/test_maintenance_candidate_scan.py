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


if __name__ == "__main__":
    unittest.main()
