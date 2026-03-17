import unittest
from types import SimpleNamespace

from app.services.status_projection_service import get_service_status_display


class StatusProjectionTests(unittest.TestCase):
    def test_off_state_beats_stale_shutdown_intent(self):
        intent_state = {"value": "shutting"}
        ctx = SimpleNamespace(
            OFF_STATES={"inactive", "failed"},
            get_service_status_intent=lambda: intent_state["value"],
            set_service_status_intent=lambda value: intent_state.__setitem__("value", value),
            is_rcon_startup_ready=lambda service_status=None: False,
        )

        display = get_service_status_display(ctx, "inactive", "0")

        self.assertEqual(display, "Off")
        self.assertIsNone(intent_state["value"])


if __name__ == "__main__":
    unittest.main()
