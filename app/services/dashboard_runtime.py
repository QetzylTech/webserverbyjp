"""Expose dashboard query and metrics helpers through one stable module."""

import time

from app.queries import dashboard_runtime_queries as _query
from app.services import metrics_aggregator as _metrics
from app.services import page_activity as _activity

_QUERY_EXPORTS = (
    "invalidate_observed_state_cache",
    "load_backup_log_cache_from_disk",
    "append_backup_log_cache_line",
    "get_cached_backup_log_text",
    "load_minecraft_log_cache_from_journal",
    "append_minecraft_log_cache_line",
    "get_cached_minecraft_log_text",
    "load_mcweb_log_cache_from_disk",
    "append_mcweb_log_cache_line",
    "get_cached_mcweb_log_text",
    "set_file_page_items",
    "refresh_file_page_items",
    "get_cached_file_page_items",
    "warm_file_page_caches",
    "file_page_cache_refresher_loop",
    "ensure_file_page_cache_refresher_started",
    "get_backups_status",
    "get_observed_state",
    "get_consistency_report",
    "reconcile_operations_once",
    "operation_reconciler_loop",
    "start_operation_reconciler",
)
_ACTIVITY_EXPORTS = (
    "mark_file_page_client_active",
    "has_active_file_page_clients",
    "mark_home_page_client_active",
    "has_active_home_page_clients",
)
_METRIC_EXPORTS = (
    "class_from_percent",
    "extract_percent",
    "usage_class_from_text",
    "get_cpu_per_core_items",
    "get_ram_usage_class",
    "get_storage_usage_class",
    "get_cpu_frequency_class",
    "slow_metrics_ttl_seconds",
    "get_slow_metrics",
    "collect_dashboard_metrics",
    "publish_metrics_snapshot",
    "collect_and_publish_metrics",
    "metrics_collector_loop",
    "ensure_metrics_collector_started",
    "get_cached_dashboard_metrics",
)

state_store_service = _query.state_store_service
_OBSERVED_OPS_CACHE = _query._OBSERVED_OPS_CACHE

for _name in _QUERY_EXPORTS:
    globals()[_name] = getattr(_query, _name)
for _name in _ACTIVITY_EXPORTS:
    globals()[_name] = getattr(_activity, _name)
for _name in _METRIC_EXPORTS:
    globals()[_name] = getattr(_metrics, _name)

del _name

__all__ = [
    "_OBSERVED_OPS_CACHE",
    "_query",
    "state_store_service",
    "time",
    *_QUERY_EXPORTS,
    *_ACTIVITY_EXPORTS,
    *_METRIC_EXPORTS,
]
