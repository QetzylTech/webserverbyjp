import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import control_plane
from app.state import BackupState


class ControlPlaneTests(unittest.TestCase):
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
             patch.object(control_plane.subprocess, "run", return_value=fake_result) as run_mock, \
             patch.object(control_plane, "get_backup_zip_snapshot", side_effect=[{}, {}]), \
             patch.object(control_plane, "backup_snapshot_changed", return_value=False) as snapshot_changed:
            ok = control_plane.run_backup_script(ctx, count_skip_as_success=False, trigger="auto")

        self.assertTrue(ok)
        run_mock.assert_called_once_with(
            [ctx.BACKUP_SCRIPT, "auto"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        snapshot_changed.assert_called_once_with(ctx, {}, {})


if __name__ == "__main__":
    unittest.main()
