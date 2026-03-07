"""Dashboard runtime facade with explicit exports."""

import time

from app.queries import dashboard_runtime_queries as _query
from app.services import metrics_aggregator as _metrics

state_store_service = _query.state_store_service
_OBSERVED_OPS_CACHE = _query._OBSERVED_OPS_CACHE

invalidate_observed_state_cache = _query.invalidate_observed_state_cache
load_backup_log_cache_from_disk = _query.load_backup_log_cache_from_disk
append_backup_log_cache_line = _query.append_backup_log_cache_line
get_cached_backup_log_text = _query.get_cached_backup_log_text
load_minecraft_log_cache_from_journal = _query.load_minecraft_log_cache_from_journal
append_minecraft_log_cache_line = _query.append_minecraft_log_cache_line
get_cached_minecraft_log_text = _query.get_cached_minecraft_log_text
load_mcweb_log_cache_from_disk = _query.load_mcweb_log_cache_from_disk
append_mcweb_log_cache_line = _query.append_mcweb_log_cache_line
get_cached_mcweb_log_text = _query.get_cached_mcweb_log_text
set_file_page_items = _query.set_file_page_items
refresh_file_page_items = _query.refresh_file_page_items
mark_file_page_client_active = _query.mark_file_page_client_active
has_active_file_page_clients = _query.has_active_file_page_clients
get_cached_file_page_items = _query.get_cached_file_page_items
warm_file_page_caches = _query.warm_file_page_caches
file_page_cache_refresher_loop = _query.file_page_cache_refresher_loop
ensure_file_page_cache_refresher_started = _query.ensure_file_page_cache_refresher_started
get_backups_status = _query.get_backups_status
get_observed_state = _query.get_observed_state
get_consistency_report = _query.get_consistency_report
reconcile_operations_once = _query.reconcile_operations_once
operation_reconciler_loop = _query.operation_reconciler_loop
start_operation_reconciler = _query.start_operation_reconciler

class_from_percent = _metrics.class_from_percent
extract_percent = _metrics.extract_percent
usage_class_from_text = _metrics.usage_class_from_text
get_cpu_per_core_items = _metrics.get_cpu_per_core_items
get_ram_usage_class = _metrics.get_ram_usage_class
get_storage_usage_class = _metrics.get_storage_usage_class
get_cpu_frequency_class = _metrics.get_cpu_frequency_class
slow_metrics_ttl_seconds = _metrics.slow_metrics_ttl_seconds
get_slow_metrics = _metrics.get_slow_metrics
collect_dashboard_metrics = _metrics.collect_dashboard_metrics
publish_metrics_snapshot = _metrics.publish_metrics_snapshot
mark_home_page_client_active = _metrics.mark_home_page_client_active
has_active_home_page_clients = _metrics.has_active_home_page_clients
collect_and_publish_metrics = _metrics.collect_and_publish_metrics
metrics_collector_loop = _metrics.metrics_collector_loop
ensure_metrics_collector_started = _metrics.ensure_metrics_collector_started
get_cached_dashboard_metrics = _metrics.get_cached_dashboard_metrics

__all__ = [
    '_OBSERVED_OPS_CACHE',
    '_query',
    'state_store_service',
    'time',
    'invalidate_observed_state_cache',
    'load_backup_log_cache_from_disk',
    'append_backup_log_cache_line',
    'get_cached_backup_log_text',
    'load_minecraft_log_cache_from_journal',
    'append_minecraft_log_cache_line',
    'get_cached_minecraft_log_text',
    'load_mcweb_log_cache_from_disk',
    'append_mcweb_log_cache_line',
    'get_cached_mcweb_log_text',
    'set_file_page_items',
    'refresh_file_page_items',
    'mark_file_page_client_active',
    'has_active_file_page_clients',
    'get_cached_file_page_items',
    'warm_file_page_caches',
    'file_page_cache_refresher_loop',
    'ensure_file_page_cache_refresher_started',
    'get_backups_status',
    'get_observed_state',
    'get_consistency_report',
    'reconcile_operations_once',
    'operation_reconciler_loop',
    'start_operation_reconciler',
    'class_from_percent',
    'extract_percent',
    'usage_class_from_text',
    'get_cpu_per_core_items',
    'get_ram_usage_class',
    'get_storage_usage_class',
    'get_cpu_frequency_class',
    'slow_metrics_ttl_seconds',
    'get_slow_metrics',
    'collect_dashboard_metrics',
    'publish_metrics_snapshot',
    'mark_home_page_client_active',
    'has_active_home_page_clients',
    'collect_and_publish_metrics',
    'metrics_collector_loop',
    'ensure_metrics_collector_started',
    'get_cached_dashboard_metrics',
]
