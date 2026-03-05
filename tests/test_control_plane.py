import unittest
import time
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import service_ops as control_plane
from app.services import restore_workflow
from app.state import BackupState


class ControlPlaneTests(unittest.TestCase):
    def test_start_restore_job_records_status_and_undo_snapshot(self):
        ctx = SimpleNamespace(
            DISPLAY_TZ=datetime.now().astimezone().tzinfo,
            restore_lock=threading.Lock(),
            restore_status_lock=threading.Lock(),
            APP_STATE_DB_PATH=Path("data/test_app_state.sqlite3"),
            log_mcweb_exception=lambda *_args, **_kwargs: None,
            restore_status={
                "job_id": "",
                "running": False,
                "seq": 0,
                "events": [],
                "result": None,
                "undo_filename": "",
            },
        )

        fake_result = {
            "ok": True,
            "message": "Restore completed successfully.",
            "pre_restore_snapshot_name": "world_2026-01-01_00-00-00_pre_restore.zip",
        }

        with patch.object(restore_workflow, "restore_world_backup", return_value=fake_result), \
             patch.object(restore_workflow.state_store_service, "append_restore_run", return_value=None), \
             patch.object(restore_workflow.state_store_service, "restore_backup_records_match", return_value=True):
            started = control_plane.start_restore_job(ctx, "world_test_manual.zip")
            self.assertTrue(started["ok"])
            deadline = datetime.now().timestamp() + 1.0
            while datetime.now().timestamp() < deadline:
                status = control_plane.get_restore_status(ctx, since_seq=0, job_id=started["job_id"])
                if not status["running"]:
                    break
                time.sleep(0.01)
            status = control_plane.get_restore_status(ctx, since_seq=0, job_id=started["job_id"])
        self.assertFalse(status["running"])
        self.assertTrue(status["result"]["ok"])
        self.assertEqual(
            fake_result["pre_restore_snapshot_name"],
            status["result"]["pre_restore_snapshot_name"],
        )
        self.assertGreaterEqual(len(status["events"]), 2)

    def test_run_backup_script_passes_ctx_to_snapshot_change_and_trigger(self):
        ctx = SimpleNamespace(
            backup_state=BackupState(
                lock=threading.Lock(),
                run_lock=threading.Lock(),
                periodic_runs=0,
                last_error="",
            ),
            BACKUP_SCRIPT=Path("scripts/backup.sh"),
        )

        fake_result = Mock(returncode=0, stdout="", stderr="")

        with patch.object(control_plane, "is_backup_running", return_value=False), \
             patch.object(control_plane._calls, "run_backup_script", return_value=fake_result) as run_mock, \
             patch.object(control_plane, "get_backup_zip_snapshot", side_effect=[{}, {}]), \
             patch.object(control_plane, "backup_snapshot_changed", return_value=False) as snapshot_changed:
            ok = control_plane.run_backup_script(ctx, count_skip_as_success=False, trigger="auto")

        self.assertTrue(ok)
        run_mock.assert_called_once_with(
            ctx.BACKUP_SCRIPT,
            "auto",
            timeout=600,
        )
        snapshot_changed.assert_called_once_with(ctx, {}, {})

    def test_restore_world_backup_rejects_when_lock_busy(self):
        lock = threading.Lock()
        lock.acquire()
        self.addCleanup(lock.release)
        ctx = SimpleNamespace(restore_lock=lock)

        result = control_plane.restore_world_backup(ctx, "world_test_manual.zip")

        self.assertFalse(result["ok"])
        self.assertIn("already in progress", result["message"])

    def test_restore_world_backup_rejects_invalid_filename(self):
        ctx = SimpleNamespace(
            restore_lock=threading.Lock(),
            _safe_filename_in_dir=Mock(return_value=None),
            BACKUP_DIR=Path("/tmp"),
        )

        result = control_plane.restore_world_backup(ctx, "../bad.zip")

        self.assertFalse(result["ok"])
        self.assertEqual("Backup file not found.", result["message"])


if __name__ == "__main__":
    unittest.main()
