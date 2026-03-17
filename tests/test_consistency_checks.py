import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from app.routes import dashboard_routes
from app.services import dashboard_operations_runtime


class ConsistencyChecksTests(unittest.TestCase):
    def test_consistency_report_detects_active_missing_session(self):
        events = []
        ctx = SimpleNamespace(
            get_status=lambda: "active",
            OFF_STATES={"inactive", "failed"},
            read_session_start_time=lambda: None,
            write_session_start_time=lambda: (events.append("write") or 1.0),
            clear_session_start_time=lambda: events.append("clear"),
        )
        report = dashboard_operations_runtime.get_consistency_report(ctx, auto_repair=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any(item.get("code") == "active_missing_session_start" for item in report["issues"]))
        self.assertIn("write", events)

    def test_consistency_report_detects_off_with_stale_session(self):
        events = []
        ctx = SimpleNamespace(
            get_status=lambda: "inactive",
            OFF_STATES={"inactive", "failed"},
            read_session_start_time=lambda: 123.0,
            write_session_start_time=lambda: (events.append("write") or 1.0),
            clear_session_start_time=lambda: events.append("clear"),
        )
        report = dashboard_operations_runtime.get_consistency_report(ctx, auto_repair=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any(item.get("code") == "off_with_session_start" for item in report["issues"]))
        self.assertIn("clear", events)

    def test_consistency_check_route_supports_password_gated_auto_repair(self):
        app = Flask(__name__)
        state = {
            "low_storage_error_message": lambda: "low",
            "_mark_home_page_client_active": lambda: None,
            "get_cached_dashboard_metrics": lambda: {
                "service_status": "Off",
                "service_status_class": "stat-red",
                "service_running_status": "inactive",
                "backups_status": "ready",
                "cpu_per_core_items": [],
                "cpu_frequency": "n/a",
                "cpu_frequency_class": "stat-red",
                "storage_usage": "n/a",
                "storage_usage_class": "stat-red",
                "players_online": "0",
                "tick_rate": "0",
                "session_duration": "--",
                "idle_countdown": "--",
                "backup_status": "Idle",
                "backup_status_class": "stat-yellow",
                "last_backup_time": "--",
                "next_backup_time": "--",
                "server_time": "--",
                "world_name": "world",
                "ram_usage": "n/a",
                "ram_usage_class": "stat-red",
                "rcon_enabled": True,
            },
            "is_storage_low": lambda: False,
            "get_log_source_text": lambda source: "",
            "_ensure_csrf_token": lambda: "t",
            "HOME_PAGE_HEARTBEAT_INTERVAL_MS": 1000,
            "log_mcweb_log": lambda *_args, **_kwargs: None,
            "FAVICON_URL": "https://example.com/favicon.ico",
            "DOCS_DIR": Path("."),
            "DOC_README_URL": "/doc/server_setup_doc.md",
            "get_device_name_map": lambda: {"127.0.0.1": "local"},
            "_get_client_ip": lambda: "127.0.0.1",
            "get_observed_state": lambda: {"service_status_display": "Off"},
            "validate_sudo_password": lambda password: password == "ok",
            "_password_rejected_response": lambda: ("password incorrect", 403),
            "record_successful_password_ip": lambda: None,
            "get_consistency_report": lambda auto_repair=False: {"ok": True, "auto_repair": bool(auto_repair), "issues": []},
        }
        with patch.object(dashboard_routes, "render_template", return_value="home-page"), \
             patch.object(dashboard_routes, "register_file_routes", lambda app, state: None), \
             patch.object(dashboard_routes, "register_metrics_routes", lambda app, state, get_nav_alert_state_from_request=None: None), \
             patch.object(dashboard_routes, "register_maintenance_routes", lambda app, state: None), \
             patch.object(dashboard_routes, "register_control_routes", lambda app, state, run_cleanup_event_if_enabled: None):
            dashboard_routes.register_routes(app, state)
            client = app.test_client()
            self.assertEqual(client.get("/consistency-check").status_code, 200)
            self.assertEqual(client.get("/consistency-check?auto_repair=1&sudo_password=bad").status_code, 403)
            ok_resp = client.get("/consistency-check?auto_repair=1&sudo_password=ok")
            self.assertEqual(ok_resp.status_code, 200)
            payload = ok_resp.get_json() or {}
            self.assertTrue((payload.get("report") or {}).get("auto_repair"))


if __name__ == "__main__":
    unittest.main()
