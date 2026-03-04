import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.routes import dashboard_routes as home_routes


class NavAlertIntentTests(unittest.TestCase):
    def _build_app(self, *, ip="10.0.0.2", device_map=None, service_status="Off"):
        app = Flask(__name__)
        metrics_payload = {"service_status": service_status}
        state = {
            "low_storage_error_message": lambda: "low",
            "_mark_home_page_client_active": lambda: None,
            "get_cached_dashboard_metrics": lambda: dict(metrics_payload),
            "is_storage_low": lambda: False,
            "HTML_TEMPLATE_NAME": "home.html",
            "get_log_source_text": lambda source: "",
            "_ensure_csrf_token": lambda: "t",
            "HOME_PAGE_HEARTBEAT_INTERVAL_MS": 1000,
            "log_mcweb_log": lambda *_args, **_kwargs: None,
            "FAVICON_URL": "https://example.com/favicon.ico",
            "DOCS_DIR": Path("."),
            "DOC_README_URL": "/doc/server_setup_doc.md",
            "get_device_name_map": lambda: dict(device_map or {}),
            "_get_client_ip": lambda: ip,
            "get_observed_state": lambda: {
                "service_status_display": str(metrics_payload.get("service_status", service_status or "Off") or "Off"),
            },
        }
        with patch.object(home_routes, "render_template", return_value="home"), \
             patch.object(home_routes, "register_debug_routes", lambda app, state: None), \
             patch.object(home_routes, "register_file_routes", lambda app, state: None), \
             patch.object(home_routes, "register_maintenance_routes", lambda app, state: None), \
             patch.object(home_routes, "register_control_routes", lambda app, state, run_cleanup_event_if_enabled: None):
            home_routes.register_routes(app, state)
        return app, metrics_payload

    def test_nav_alert_state_prefers_device_name_over_ip(self):
        app, _ = self._build_app(device_map={"10.0.0.2": "Alice-Laptop"}, service_status="Off")
        client = app.test_client()
        self.assertEqual(
            client.post("/maintenance/nav-alert/restore-pane-open", json={"filename": "world.zip", "client_id": "c1"}).status_code,
            204,
        )
        state_res = client.get("/maintenance/nav-alert/state", headers={"X-MCWEB-Client-Id": "c2"})
        self.assertEqual(state_res.status_code, 200)
        payload = state_res.get_json()
        self.assertTrue(payload["restore_pane_attention"])
        self.assertEqual(payload["restore_pane_opened_by_name"], "Alice-Laptop")
        self.assertEqual(payload["restore_pane_opened_by_ip"], "10.0.0.2")

    def test_nav_alert_state_uses_ip_when_name_unavailable(self):
        app, _ = self._build_app(device_map={}, service_status="Off")
        client = app.test_client()
        client.post("/maintenance/nav-alert/restore-pane-open", json={"filename": "world.zip"})
        payload = client.get("/maintenance/nav-alert/state").get_json()
        self.assertEqual(payload["restore_pane_opened_by_name"], "10.0.0.2")

    def test_nav_alert_state_detects_opened_by_self_via_client_id(self):
        app, _ = self._build_app(device_map={"10.0.0.2": "Alice-Laptop"}, service_status="Off")
        client = app.test_client()
        client.post("/maintenance/nav-alert/restore-pane-open", json={"filename": "x.zip", "client_id": "same"})
        payload = client.get("/maintenance/nav-alert/state", headers={"X-MCWEB-Client-Id": "same"}).get_json()
        self.assertTrue(payload["restore_pane_opened_by_self"])

    def test_nav_alert_state_home_attention_color_by_service_status(self):
        app_red, metrics_red = self._build_app(service_status="Crashed")
        payload_red = app_red.test_client().get("/maintenance/nav-alert/state").get_json()
        self.assertEqual(payload_red["home_attention"], "red")
        metrics_red["service_status"] = "Starting"
        payload_yellow = app_red.test_client().get("/maintenance/nav-alert/state").get_json()
        self.assertEqual(payload_yellow["home_attention"], "yellow")


if __name__ == "__main__":
    unittest.main()
