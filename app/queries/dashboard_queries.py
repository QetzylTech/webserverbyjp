"""Read-side dashboard/status query models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DashboardQueryDeps:
    low_storage_error_message: Callable[[], str]
    mark_home_page_client_active: Callable[[], None]
    get_cached_dashboard_metrics: Callable[[], dict[str, Any]]
    is_storage_low: Callable[[], bool]
    get_observed_state: Callable[[], dict[str, Any]]
    get_consistency_report: Callable[..., dict[str, Any]]

    @staticmethod
    def from_state(state: Any) -> "DashboardQueryDeps":
        if hasattr(state, "ctx"):
            state = state.ctx
        if isinstance(state, dict):
            return DashboardQueryDeps(
                low_storage_error_message=state["low_storage_error_message"],
                mark_home_page_client_active=state["_mark_home_page_client_active"],
                get_cached_dashboard_metrics=state["get_cached_dashboard_metrics"],
                is_storage_low=state["is_storage_low"],
                get_observed_state=state["get_observed_state"],
                get_consistency_report=state.get("get_consistency_report", lambda auto_repair=False: {}),
            )
        return DashboardQueryDeps(
            low_storage_error_message=getattr(state, "low_storage_error_message"),
            mark_home_page_client_active=getattr(state, "_mark_home_page_client_active"),
            get_cached_dashboard_metrics=getattr(state, "get_cached_dashboard_metrics"),
            is_storage_low=getattr(state, "is_storage_low"),
            get_observed_state=getattr(state, "get_observed_state"),
            get_consistency_report=getattr(state, "get_consistency_report", lambda auto_repair=False: {}),
        )


def resolve_alert_message(deps: DashboardQueryDeps, message_code: str):
    code = str(message_code or "").strip()
    if code == "password_incorrect":
        return code, "Password incorrect. Action rejected."
    if code == "csrf_invalid":
        return code, "Security check failed. Please refresh and try again."
    if code == "session_write_failed":
        return code, "Session file write failed."
    if code == "backup_failed":
        return code, "Backup failed."
    if code == "internal_error":
        return code, "Internal server error."
    if code == "low_storage_space":
        return code, deps.low_storage_error_message()
    if code == "start_failed":
        return code, "Server failed to start."
    return code, ""


def get_dashboard_home_model(state_or_deps: Any, message_code: str):
    deps = state_or_deps if isinstance(state_or_deps, DashboardQueryDeps) else DashboardQueryDeps.from_state(state_or_deps)
    resolved_code, alert_message = resolve_alert_message(deps, message_code)
    deps.mark_home_page_client_active()
    data = deps.get_cached_dashboard_metrics()
    if deps.is_storage_low():
        resolved_code = "low_storage_space"
        alert_message = deps.low_storage_error_message()
        data["low_storage_blocked"] = True
        data["low_storage_message"] = alert_message
    return {
        "message_code": resolved_code,
        "alert_message": alert_message,
        "metrics": data,
    }


def get_observed_state_model(state_or_deps: Any):
    deps = state_or_deps if isinstance(state_or_deps, DashboardQueryDeps) else DashboardQueryDeps.from_state(state_or_deps)
    return {"ok": True, "observed": deps.get_observed_state()}


def get_consistency_report_model(state_or_deps: Any, *, auto_repair=False):
    deps = state_or_deps if isinstance(state_or_deps, DashboardQueryDeps) else DashboardQueryDeps.from_state(state_or_deps)
    return {"ok": True, "report": deps.get_consistency_report(auto_repair=bool(auto_repair))}


def get_home_attention_level(observed):
    service_status = str((observed or {}).get("service_status_display", "") or "").strip().lower()
    if service_status == "crashed":
        return "red"
    if service_status in {"starting", "shutting down"}:
        return "yellow"
    if service_status == "running":
        return "green"
    return "none"
