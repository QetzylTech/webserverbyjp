import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from unittest.mock import patch

from app.core.filesystem_utils import list_download_files
from app.services import dashboard_runtime


class DashboardBackupsListingTests(unittest.TestCase):
    def test_refresh_file_page_items_backups_includes_zip_and_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backups"
            snapshot_root = backup_dir / "snapshots"
            backup_dir.mkdir(parents=True)
            snapshot_root.mkdir(parents=True)

            (backup_dir / "world_2026-01-01_manual.zip").write_text("zip", encoding="utf-8")
            snap_dir = snapshot_root / "world_2026-01-01_auto"
            snap_dir.mkdir()
            (snap_dir / "level.dat").write_text("data", encoding="utf-8")

            ctx = SimpleNamespace(
                BACKUP_DIR=backup_dir,
                AUTO_SNAPSHOT_DIR=snapshot_root,
                DISPLAY_TZ=ZoneInfo("UTC"),
                APP_STATE_DB_PATH=root / "app_state.sqlite3",
                _list_download_files=lambda base, pattern, tz: list_download_files(base, pattern, tz),
                file_page_cache_lock=threading.Lock(),
                file_page_cache={},
                log_mcweb_exception=lambda *_args, **_kwargs: None,
            )

            with patch.object(dashboard_runtime.state_store_service, "replace_file_records_snapshot", return_value=None):
                items = dashboard_runtime.refresh_file_page_items(ctx, "backups")

            names = {item["name"] for item in items}
            self.assertIn("world_2026-01-01_manual.zip", names)
            self.assertIn("world_2026-01-01_auto", names)

            snapshot_item = next(item for item in items if item["name"] == "world_2026-01-01_auto")
            self.assertEqual("snapshot::world_2026-01-01_auto", snapshot_item["restore_name"])
            self.assertEqual("/download/backups-snapshot/world_2026-01-01_auto", snapshot_item["download_url"])
            self.assertEqual("world_2026-01-01_auto.zip", snapshot_item["download_name"])


if __name__ == "__main__":
    unittest.main()
