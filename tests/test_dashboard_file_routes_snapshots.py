import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.core import state_store as state_store_service
from app.routes.dashboard_file_routes import register_file_routes
from app.routes.dashboard_metrics_routes import register_metrics_routes
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


class MetricsRouteTests(unittest.TestCase):
    def test_metrics_route_returns_snapshot_without_route_side_status_rewrite(self):
        app = Flask(__name__)
        app.testing = True
        state = {
            "BACKUP_DIR": Path("."),
            "APP_STATE_DB_PATH": Path("state.sqlite3"),
            "get_cached_dashboard_metrics": lambda: {
                "service_status": "Off",
                "service_status_class": "stat-red",
                "service_running_status": "inactive",
            },
        }
        register_metrics_routes(app, state)

        with patch.object(state_store_service, "get_latest_event", return_value=None), patch.object(
            state_store_service,
            "list_operations_by_status",
            side_effect=AssertionError("stale route-side status rewrite should not run"),
        ):
            response = app.test_client().get("/metrics")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload.get("service_status"), "Off")
        self.assertEqual(payload.get("service_running_status"), "inactive")

    def test_metrics_route_best_effort_refresh_runs_in_all_role(self):
        app = Flask(__name__)
        app.testing = True
        calls = {"publish": 0}
        state = {
            "PROCESS_ROLE": "all",
            "BACKUP_DIR": Path("."),
            "APP_STATE_DB_PATH": Path("state.sqlite3"),
            "ensure_metrics_collector_started": lambda: None,
            "_collect_and_publish_metrics": lambda: calls.__setitem__("publish", calls["publish"] + 1),
            "get_cached_dashboard_metrics": lambda: {"service_status": "Off"},
        }
        register_metrics_routes(app, state)

        with patch.object(state_store_service, "get_latest_event", return_value=None):
            response = app.test_client().get("/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls["publish"], 1)


if __name__ == "__main__":
    unittest.main()
