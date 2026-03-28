"""Build runtime delegates from explicit service method maps."""

from __future__ import annotations

import re
import time
from typing import Any, Callable

_DASHBOARD_FILE_METHODS = (
    "_mark_file_page_client_active",
    "get_cached_file_page_items",
    "file_page_cache_refresher_loop",
    "ensure_file_page_cache_refresher_started",
    "warm_file_page_caches",
)
_CONTROL_METHODS = (
    "set_service_status_intent",
    "get_service_status_intent",
    "stop_service_runtime",
    "run_elevated_command",
    "validate_sudo_password",
    "validate_admin_password",
    "validate_superadmin_password",
    "graceful_stop_minecraft",
    "stop_server_automatically",
    "ensure_startup_rcon_settings",
    "start_service_non_blocking",
    "run_backup_script",
    "restore_world_backup",
    "append_restore_event",
    "start_restore_job",
    "get_restore_status",
    "format_backup_time",
    "get_server_time_text",
    "get_latest_backup_zip_timestamp",
    "get_backup_zip_snapshot",
    "backup_snapshot_changed",
    "get_backup_schedule_times",
    "get_backup_status",
    "is_backup_running",
    "reset_backup_schedule_state",
)
_SESSION_METHODS = (
    "ensure_session_file",
    "read_session_start_time",
    "write_session_start_time",
    "clear_session_start_time",
    "get_session_start_time",
    "get_session_duration_text",
)
_MINECRAFT_CTX_METHODS = (
    "_log_source_settings",
    "get_log_source_text",
    "_drain_buffered_log_lines",
    "ensure_log_stream_fetcher_started",
    "flush_log_stream_batch",
    "_increment_log_stream_clients",
    "_decrement_log_stream_clients",
    "_refresh_rcon_config",
    "is_rcon_enabled",
    "is_rcon_startup_ready",
    "_run_mcrcon",
    "_probe_minecraft_runtime_metrics",
    "get_players_online",
    "get_tick_rate",
    "get_service_status_display",
)
_MINECRAFT_PLAIN_METHODS = ("get_service_status_class",)
_DASHBOARD_STATE_METHODS = (
    "get_backups_status",
    "get_observed_state",
    "invalidate_observed_state_cache",
)
_DASHBOARD_METRIC_METHODS = (
    "get_cpu_per_core_items",
    "get_ram_usage_class",
    "get_storage_usage_class",
    "get_cpu_frequency_class",
    "collect_dashboard_metrics",
    "_mark_home_page_client_active",
    "_collect_and_publish_metrics",
    "metrics_collector_loop",
    "ensure_metrics_collector_started",
    "get_cached_dashboard_metrics",
)
_DASHBOARD_OPERATION_METHODS = (
    "get_consistency_report",
    "start_operation_reconciler",
)
_SESSION_WATCHER_CTX_METHODS = (
    "get_idle_countdown",
    "idle_player_watcher",
    "start_idle_player_watcher",
    "backup_session_watcher",
    "start_backup_session_watcher",
    "storage_safety_watcher",
    "start_storage_safety_watcher",
    "initialize_session_tracking",
    "_status_state_note",
)
_SESSION_WATCHER_PLAIN_METHODS = ("format_countdown",)


def build_runtime_bindings(
    namespace: dict[str, Any],
    *,
    dashboard_file_runtime_service: Any,
    dashboard_state_runtime_service: Any,
    dashboard_metrics_runtime_service: Any,
    dashboard_operations_runtime_service: Any,
    control_plane_service: Any,
    minecraft_runtime_service: Any,
    session_watchers_service: Any,
) -> dict[str, Callable[..., Any] | Any]:
    """Return namespace-aware delegates for runtime state and services."""
    ns = namespace

    def _state() -> Any:
        return ns["STATE"].ctx

    def _ctx_delegate(service: Any, method_name: str) -> Callable[..., Any]:
        method = getattr(service, method_name)

        def bound(*args: Any, **kwargs: Any) -> Any:
            return method(_state(), *args, **kwargs)

        return bound

    def _plain_delegate(service: Any, method_name: str) -> Callable[..., Any]:
        method = getattr(service, method_name)

        def bound(*args: Any, **kwargs: Any) -> Any:
            return method(*args, **kwargs)

        return bound

    def _bind_methods(
        service: Any,
        method_names: tuple[str, ...],
        binder: Callable[[Any, str], Callable[..., Any]],
    ) -> dict[str, Callable[..., Any]]:
        return {name: binder(service, name.lstrip("_")) for name in method_names}

    bindings: dict[str, Callable[..., Any] | Any] = {}
    bindings.update(_bind_methods(dashboard_file_runtime_service, _DASHBOARD_FILE_METHODS, _ctx_delegate))
    bindings.update(_bind_methods(control_plane_service, _CONTROL_METHODS, _ctx_delegate))
    bindings.update(_bind_methods(control_plane_service, _SESSION_METHODS, _ctx_delegate))
    bindings.update(_bind_methods(minecraft_runtime_service, _MINECRAFT_CTX_METHODS, _ctx_delegate))
    bindings.update(_bind_methods(minecraft_runtime_service, _MINECRAFT_PLAIN_METHODS, _plain_delegate))
    bindings.update(_bind_methods(dashboard_state_runtime_service, _DASHBOARD_STATE_METHODS, _ctx_delegate))
    bindings.update(_bind_methods(dashboard_metrics_runtime_service, _DASHBOARD_METRIC_METHODS, _ctx_delegate))
    bindings.update(_bind_methods(dashboard_operations_runtime_service, _DASHBOARD_OPERATION_METHODS, _ctx_delegate))
    bindings.update(_bind_methods(session_watchers_service, _SESSION_WATCHER_CTX_METHODS, _ctx_delegate))
    bindings.update(_bind_methods(session_watchers_service, _SESSION_WATCHER_PLAIN_METHODS, _plain_delegate))


    def _get_storage_guard() -> Any:
        guard = ns.get("storage_guard")
        return guard

    def get_storage_used_percent(storage_usage_text: str | None = None) -> float | None:
        usage_text = storage_usage_text if storage_usage_text is not None else ns["get_storage_usage"]()
        match = re.search(r"\(([\d.]+)%\)", usage_text or "")
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def get_storage_available_percent(storage_usage_text: str | None = None) -> float | None:
        used = get_storage_used_percent(storage_usage_text)
        if used is None:
            return None
        return max(0.0, 100.0 - used)

    def is_storage_low(storage_usage_text: str | None = None) -> bool:
        guard = _get_storage_guard()
        if guard is not None and storage_usage_text is None:
            try:
                return bool(guard.is_below_minimum(ns))
            except Exception:
                pass
        available = get_storage_available_percent(storage_usage_text)
        if available is None:
            return False
        threshold = float(ns["LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT"])
        return available < threshold

    def low_storage_error_message(storage_usage_text: str | None = None) -> str:
        guard = _get_storage_guard()
        if guard is not None and storage_usage_text is None:
            try:
                return str(guard.block_message(ns, "start"))
            except Exception:
                pass
        usage_text = storage_usage_text if storage_usage_text is not None else ns["get_storage_usage"]()
        available = get_storage_available_percent(usage_text)
        available_text = "unknown"
        if available is not None:
            available_text = f"{available:.1f}%"
        return (
            f"Low storage space: only {available_text} free ({usage_text}). "
            f"Starting is blocked below {ns['LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT']:.0f}% free."
        )

    def set_backup_warning(message: Any) -> None:
        msg = str(message or "").strip()
        with ns["backup_warning_lock"]:
            ns["backup_warning_seq"] += 1
            ns["backup_warning_message"] = msg
            ns["backup_warning_at"] = time.time()

    def get_backup_warning_state(ttl_seconds: float | None = None) -> dict[str, Any]:
        ttl = ns["BACKUP_WARNING_TTL_SECONDS"] if ttl_seconds is None else float(ttl_seconds)
        with ns["backup_warning_lock"]:
            seq = int(ns["backup_warning_seq"])
            msg = str(ns["backup_warning_message"] or "")
            at = float(ns["backup_warning_at"] or 0.0)
        if not msg:
            return {"seq": seq, "message": ""}
        if ttl > 0 and (time.time() - at) > ttl:
            return {"seq": seq, "message": ""}
        return {"seq": seq, "message": msg}

    bindings.update(
        {
            "get_storage_used_percent": get_storage_used_percent,
            "get_storage_available_percent": get_storage_available_percent,
            "is_storage_low": is_storage_low,
            "low_storage_error_message": low_storage_error_message,
            "set_backup_warning": set_backup_warning,
            "get_backup_warning_state": get_backup_warning_state,
        }
    )
    return bindings
