from app.infrastructure.adapters import PlatformMetricsAdapter


def test_platform_metrics_adapter_keeps_cpu_per_core_as_list(monkeypatch):
    adapter = PlatformMetricsAdapter()

    class FakeMetrics:
        @staticmethod
        def get_cpu_usage_per_core():
            return ["1.0", "2.0"]

    monkeypatch.setattr(adapter, "_metrics", FakeMetrics())

    assert adapter.get_cpu_usage_per_core() == ["1.0", "2.0"]
