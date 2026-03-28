import unittest
from unittest.mock import patch

from flask import Flask

from app.routes import dashboard_notifications_routes
from app.routes.dashboard_notifications_routes import register_notification_routes


class DashboardNotificationRoutesTests(unittest.TestCase):
    def test_notifications_stream_skips_stale_notifications_on_fresh_connect(self):
        app = Flask(__name__)
        register_notification_routes(app, {"APP_STATE_DB_PATH": "state.sqlite3"})

        with app.test_request_context("/notifications-stream"), \
             patch.object(dashboard_notifications_routes.state_store_service, "get_latest_event", return_value={"id": 9}), \
             patch.object(dashboard_notifications_routes.state_store_service, "list_events_since", return_value=[]):
            response = app.view_functions["notifications_stream"]()
            try:
                first_chunk = next(response.response)
            finally:
                response.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(first_chunk, ": keepalive\n\n")

    def test_notifications_stream_replays_new_notifications_from_explicit_since(self):
        app = Flask(__name__)
        register_notification_routes(app, {"APP_STATE_DB_PATH": "state.sqlite3"})
        rows = [
            {
                "id": 12,
                "payload": {
                    "notification": {
                        "code": "password_throttle",
                        "message": "newer",
                    }
                },
            }
        ]

        with app.test_request_context("/notifications-stream?since=11"), \
             patch.object(dashboard_notifications_routes.state_store_service, "list_events_since", return_value=rows), \
             patch.object(dashboard_notifications_routes.state_store_service, "get_latest_event") as latest_event:
            response = app.view_functions["notifications_stream"]()
            try:
                first_chunk = next(response.response)
            finally:
                response.close()

        latest_event.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: notification", first_chunk)
        self.assertIn("message\":\"newer", first_chunk)
        self.assertIn("id: 12", first_chunk)

    def test_operation_stream_replays_new_operation_updates_from_explicit_since(self):
        app = Flask(__name__)
        register_notification_routes(app, {"APP_STATE_DB_PATH": "state.sqlite3"})
        rows = [
            {
                "id": 21,
                "payload": {
                    "operation": {
                        "op_id": "start-123",
                        "op_type": "start",
                        "status": "in_progress",
                    }
                },
            }
        ]

        with app.test_request_context("/operation-stream?since=20"), \
             patch.object(dashboard_notifications_routes.state_store_service, "list_events_since", return_value=rows), \
             patch.object(dashboard_notifications_routes.state_store_service, "get_latest_event") as latest_event:
            response = app.view_functions["operation_stream"]()
            try:
                first_chunk = next(response.response)
            finally:
                response.close()

        latest_event.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: operation", first_chunk)
        self.assertIn("op_id\":\"start-123", first_chunk)
        self.assertIn("id: 21", first_chunk)


if __name__ == "__main__":
    unittest.main()
