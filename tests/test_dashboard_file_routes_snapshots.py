import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.routes.dashboard_file_routes import register_file_routes


class SnapshotDownloadRouteTests(unittest.TestCase):
    def _build_app(self, backup_dir):
        app = Flask(__name__)
        app.testing = True

        events = []

        state = {
            "BACKUP_DIR": Path(backup_dir),
            "validate_sudo_password": lambda password: password == "ok",
            "_password_rejected_response": lambda: ("password incorrect", 403),
            "record_successful_password_ip": lambda: None,
            "log_mcweb_action": lambda action, **kwargs: events.append((action, kwargs)),
        }
        register_file_routes(app, state)
        return app, events

    def test_download_snapshot_success_returns_zip_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup_dir = Path(tmp) / "backups"
            snapshot_dir = backup_dir / "snapshots" / "snap_a"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "level.dat").write_text("data", encoding="utf-8")

            app, _ = self._build_app(backup_dir)
            client = app.test_client()
            response = client.post(
                "/download/backups-snapshot/snap_a",
                data={"sudo_password": "ok"},
            )

            self.assertEqual(response.status_code, 200)
            disposition = response.headers.get("Content-Disposition", "")
            self.assertIn("attachment", disposition.lower())
            self.assertIn("snap_a.zip", disposition)
            self.assertTrue(response.data.startswith(b"PK"))

    def test_download_snapshot_wrong_password_returns_password_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup_dir = Path(tmp) / "backups"
            snapshot_dir = backup_dir / "snapshots" / "snap_a"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "level.dat").write_text("data", encoding="utf-8")

            app, _ = self._build_app(backup_dir)
            client = app.test_client()
            response = client.post(
                "/download/backups-snapshot/snap_a",
                data={"sudo_password": "bad"},
            )

            self.assertEqual(response.status_code, 403)
            self.assertIn(b"password incorrect", response.data.lower())

    def test_download_snapshot_invalid_path_or_name_returns_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup_dir = Path(tmp) / "backups"
            backup_dir.mkdir(parents=True)
            app, _ = self._build_app(backup_dir)
            client = app.test_client()

            missing = client.post(
                "/download/backups-snapshot/not_found",
                data={"sudo_password": "ok"},
            )
            self.assertEqual(missing.status_code, 404)

            traversal = client.post(
                "/download/backups-snapshot/..%2Fbad",
                data={"sudo_password": "ok"},
            )
            self.assertEqual(traversal.status_code, 404)


if __name__ == "__main__":
    unittest.main()
