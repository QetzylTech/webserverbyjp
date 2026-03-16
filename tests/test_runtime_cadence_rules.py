import threading
import unittest
from types import SimpleNamespace

from app.services import dashboard_metrics_runtime
from app.services import log_stream_service


class RuntimeCadenceRulesTests(unittest.TestCase):
    def test_refresh_background_storage_metrics_updates_only_storage_cache(self):
        ctx = SimpleNamespace(
            slow_metrics_lock=threading.Lock(),
            slow_metrics_cache={"cpu_frequency": "2.0 GHz", "storage_usage": "old"},
            slow_metrics_cache_status="inactive",
            slow_metrics_cache_at=0.0,
            get_storage_usage=lambda: "10 GiB (50%)",
        )

        dashboard_metrics_runtime.refresh_background_storage_metrics(ctx)

        self.assertEqual("10 GiB (50%)", ctx.slow_metrics_cache.get("storage_usage"))
        self.assertEqual("2.0 GHz", ctx.slow_metrics_cache.get("cpu_frequency"))
        self.assertEqual("active", ctx.slow_metrics_cache_status)
        self.assertGreater(ctx.slow_metrics_cache_at, 0.0)

    def test_should_allow_background_log_follow_minecraft_on_when_no_clients(self):
        ctx = SimpleNamespace(
            OFF_STATES={"inactive", "failed"},
            get_status=lambda: "active",
            get_service_status_intent=lambda: "",
        )
        self.assertTrue(log_stream_service.should_allow_background_log_follow(ctx, "minecraft"))

    def test_should_allow_background_log_follow_minecraft_off_and_not_starting(self):
        ctx = SimpleNamespace(
            OFF_STATES={"inactive", "failed"},
            get_status=lambda: "inactive",
            get_service_status_intent=lambda: "",
        )
        self.assertFalse(log_stream_service.should_allow_background_log_follow(ctx, "minecraft"))

    def test_should_allow_background_log_follow_backup_only_when_queued_or_running(self):
        queued_ctx = SimpleNamespace(get_backup_status=lambda: ("Queued", "stat-yellow"))
        idle_ctx = SimpleNamespace(get_backup_status=lambda: ("Idle", "stat-yellow"))

        self.assertTrue(log_stream_service.should_allow_background_log_follow(queued_ctx, "backup"))
        self.assertFalse(log_stream_service.should_allow_background_log_follow(idle_ctx, "backup"))


if __name__ == "__main__":
    unittest.main()
