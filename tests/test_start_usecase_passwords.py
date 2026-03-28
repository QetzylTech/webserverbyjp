import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from werkzeug.security import generate_password_hash

from app.services import start_usecase


class StartUsecasePasswordTests(unittest.TestCase):
    def _ctx(self):
        return SimpleNamespace(
            ADMIN_PASSWORD_HASH=generate_password_hash("admin-pass"),
            SUPERADMIN_PASSWORD_HASH=generate_password_hash("super-pass"),
            REQUIRE_SUDO_PASSWORD=True,
            password_throttle_lock=threading.Lock(),
            password_throttle_state={"by_ip": {}},
            _get_client_ip=lambda: "100.64.0.9",
            log_mcweb_action=Mock(),
        )

    def test_validate_superadmin_password_uses_superadmin_hash(self):
        ctx = self._ctx()

        self.assertFalse(start_usecase.validate_superadmin_password(ctx, "admin-pass"))
        self.assertTrue(start_usecase.validate_superadmin_password(ctx, "super-pass"))

    def test_validate_superadmin_password_uses_shared_throttle_notifications(self):
        ctx = self._ctx()

        with patch.object(start_usecase._notification_service, "publish_ui_notification") as publish_ui_notification:
            self.assertFalse(start_usecase.validate_superadmin_password(ctx, "wrong-1"))
            self.assertFalse(start_usecase.validate_superadmin_password(ctx, "wrong-2"))
            self.assertFalse(start_usecase.validate_superadmin_password(ctx, "wrong-3"))

        throttle_entry = ctx.password_throttle_state["by_ip"]["100.64.0.9"]
        self.assertGreater(float(throttle_entry["blocked_until"]), time.time())
        publish_ui_notification.assert_called_once()
        self.assertEqual(
            publish_ui_notification.call_args.args[1]["code"],
            "password_throttle",
        )


if __name__ == "__main__":
    unittest.main()
