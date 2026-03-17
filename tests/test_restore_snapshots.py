import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo
import zipfile

from app.services import restore_execution


class RestoreSnapshotTests(unittest.TestCase):
    def test_restore_world_backup_rejects_invalid_snapshot_traversal(self):
        ctx = SimpleNamespace(
            restore_lock=threading.Lock(),
            BACKUP_DIR=Path("/tmp/backups"),
            AUTO_SNAPSHOT_DIR=Path("/tmp/backups/snapshots"),
        )

        result = restore_execution.restore_world_backup(ctx, "snapshot::../bad")

        self.assertFalse(result["ok"])
        self.assertEqual("Snapshot not found.", result["message"])

    def test_restore_world_backup_snapshot_branch_runs_prerestore_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backups"
            snapshot_root = backup_dir / "snapshots"
            snapshot_dir = snapshot_root / "snap_auto_01"
            world_dir = root / "minecraft" / "world_live"
            archived_world_dir = root / "data" / "old_worlds" / "archived_world"
            props_path = root / "minecraft" / "server.properties"
            session_file = root / "data" / "session.txt"

            snapshot_dir.mkdir(parents=True)
            world_dir.mkdir(parents=True)
            props_path.parent.mkdir(parents=True, exist_ok=True)
            session_file.parent.mkdir(parents=True, exist_ok=True)
            (snapshot_dir / "level.dat").write_text("snapshot", encoding="utf-8")
            (world_dir / "level.dat").write_text("live", encoding="utf-8")
            props_path.write_text("level-name=world_live\n", encoding="utf-8")
            session_file.write_text("", encoding="utf-8")

            backup_state = SimpleNamespace(lock=threading.Lock(), periodic_runs=0)
            ctx = SimpleNamespace(
                restore_lock=threading.Lock(),
                DISPLAY_TZ=ZoneInfo("UTC"),
                BACKUP_DIR=backup_dir,
                AUTO_SNAPSHOT_DIR=snapshot_root,
                WORLD_DIR=world_dir,
                SERVER_PROPERTIES_CANDIDATES=[props_path],
                DEBUG_ENABLED=False,
                SERVICE="minecraft",
                APP_STATE_DB_PATH=root / "app_state.sqlite3",
                session_state=SimpleNamespace(session_file=session_file),
                backup_state=backup_state,
                get_status=lambda: "inactive",
                invalidate_status_cache=lambda: None,
                log_mcweb_action=lambda *_args, **_kwargs: None,
                log_mcweb_exception=lambda *_args, **_kwargs: None,
            )

            with patch.object(restore_execution, "_archive_old_world_dir", return_value=(archived_world_dir, "")) as archive_mock, \
                 patch.object(restore_execution, "_record_restore_history", return_value=True), \
                 patch.object(restore_execution, "_new_restore_code", side_effect=["abc12", "def34"]), \
                 patch.object(restore_execution, "is_backup_running", return_value=False), \
                 patch.object(restore_execution.state_store_service, "append_restore_name_run", return_value=None):
                result = restore_execution.restore_world_backup(ctx, "snapshot::snap_auto_01")

            self.assertTrue(result["ok"])
            self.assertEqual("snapshot::snap_auto_01", result["backup_file"])
            self.assertTrue(Path(result["pre_restore_snapshot"]).exists())
            archive_mock.assert_called_once()

            updated_props = props_path.read_text(encoding="utf-8")
            self.assertIn("level-name=", updated_props)
            self.assertIn("(Rxdef34)", updated_props)

    def test_restore_world_backup_rejects_unsafe_zip_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backups"
            backup_dir.mkdir(parents=True)
            world_dir = root / "minecraft" / "world_live"
            world_dir.mkdir(parents=True)
            props_path = root / "minecraft" / "server.properties"
            props_path.parent.mkdir(parents=True, exist_ok=True)
            props_path.write_text("level-name=world_live\n", encoding="utf-8")

            bad_zip = backup_dir / "bad_manual.zip"
            with zipfile.ZipFile(bad_zip, "w") as zf:
                zf.writestr("../escape.txt", "bad")

            ctx = SimpleNamespace(
                restore_lock=threading.Lock(),
                DISPLAY_TZ=ZoneInfo("UTC"),
                BACKUP_DIR=backup_dir,
                WORLD_DIR=world_dir,
                SERVER_PROPERTIES_CANDIDATES=[props_path],
                get_status=lambda: "inactive",
                log_mcweb_exception=lambda *_args, **_kwargs: None,
                _safe_filename_in_dir=lambda base_dir, name: name if name == "bad_manual.zip" else None,
            )

            with patch.object(restore_execution, "is_backup_running", return_value=False):
                result = restore_execution.restore_world_backup(ctx, "bad_manual.zip")
            self.assertFalse(result["ok"])
            self.assertIn("unsafe paths", result["message"].lower())

    def test_restore_failure_after_stop_attempts_restart_when_was_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backups"
            snapshot_root = backup_dir / "snapshots"
            snapshot_dir = snapshot_root / "snap_auto_01"
            world_dir = root / "minecraft" / "world_live"
            props_path = root / "minecraft" / "server.properties"
            session_file = root / "data" / "session.txt"
            snapshot_dir.mkdir(parents=True)
            world_dir.mkdir(parents=True)
            props_path.parent.mkdir(parents=True, exist_ok=True)
            session_file.parent.mkdir(parents=True, exist_ok=True)
            (snapshot_dir / "level.dat").write_text("snapshot", encoding="utf-8")
            props_path.write_text("level-name=world_live\n", encoding="utf-8")

            ctx = SimpleNamespace(
                restore_lock=threading.Lock(),
                DISPLAY_TZ=ZoneInfo("UTC"),
                BACKUP_DIR=backup_dir,
                AUTO_SNAPSHOT_DIR=snapshot_root,
                WORLD_DIR=world_dir,
                SERVER_PROPERTIES_CANDIDATES=[props_path],
                DEBUG_ENABLED=False,
                SERVICE="minecraft",
                APP_STATE_DB_PATH=root / "app_state.sqlite3",
                session_state=SimpleNamespace(session_file=session_file),
                backup_state=SimpleNamespace(lock=threading.Lock(), periodic_runs=0),
                get_status=lambda: "active",
                invalidate_status_cache=lambda: None,
                log_mcweb_action=lambda *_args, **_kwargs: None,
                log_mcweb_exception=lambda *_args, **_kwargs: None,
            )
            calls = []

            def _fail_copy(_source, _target):
                raise RuntimeError("copy failed")

            def _start_service(_ctx):
                calls.append(["service_start", "minecraft"])
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(restore_execution, "_copy_world_tree", side_effect=_fail_copy), \
                 patch.object(restore_execution, "start_service", side_effect=_start_service), \
                 patch.object(restore_execution, "stop_service_systemd", return_value=True), \
                 patch.object(restore_execution, "is_backup_running", return_value=False), \
                 patch.object(restore_execution, "_new_restore_code", side_effect=["abc12", "def34"]), \
                 patch.object(restore_execution, "_archive_old_world_dir", return_value=(root / "archived", "")), \
                 patch.object(restore_execution, "_record_restore_history", return_value=True), \
                 patch.object(restore_execution, "write_session_start_time", return_value=1.0) as write_session_mock:
                result = restore_execution.restore_world_backup(ctx, "snapshot::snap_auto_01")

            self.assertFalse(result["ok"])
            self.assertTrue(any(cmd[:2] == ["service_start", "minecraft"] for cmd in calls))
            write_session_mock.assert_called()


if __name__ == "__main__":
    unittest.main()

