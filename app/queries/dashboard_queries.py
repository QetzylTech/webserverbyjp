"""Read-side models for lightweight dashboard shells and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DashboardQueryDeps:
    """Minimal read-only dependencies used by dashboard query helpers."""

    low_storage_error_message: Callable[[], str]
    get_observed_state: Callable[[], dict[str, Any]]
    get_consistency_report: Callable[..., dict[str, Any]]

    @staticmethod
    def from_state(state: Any) -> "DashboardQueryDeps":
        if hasattr(state, "ctx"):
            state = state.ctx
        if isinstance(state, dict):
            return DashboardQueryDeps(
                low_storage_error_message=state["low_storage_error_message"],
                get_observed_state=state["get_observed_state"],
                get_consistency_report=state.get("get_consistency_report", lambda auto_repair=False: {}),
            )
        return DashboardQueryDeps(
            low_storage_error_message=getattr(state, "low_storage_error_message"),
            get_observed_state=getattr(state, "get_observed_state"),
            get_consistency_report=getattr(state, "get_consistency_report", lambda auto_repair=False: {}),
        )


def _coerce_deps(state_or_deps: Any) -> DashboardQueryDeps:
    return state_or_deps if isinstance(state_or_deps, DashboardQueryDeps) else DashboardQueryDeps.from_state(state_or_deps)


def _resolve_alert_message(deps: DashboardQueryDeps, message_code: str) -> tuple[str, str]:
    """Translate a compact message code into the UI text shown after redirects."""

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


def get_dashboard_shell_model(state_or_deps: Any, message_code: str) -> dict[str, str]:
    """Return the tiny server-rendered payload needed by the home-page shell."""

    deps = _coerce_deps(state_or_deps)
    resolved_code, alert_message = _resolve_alert_message(deps, message_code)
    return {
        "message_code": resolved_code,
        "alert_message": alert_message,
    }


def get_observed_state_model(state_or_deps: Any) -> dict[str, Any]:
    """Return the observed runtime state used by diagnostics and offline recovery."""

    deps = _coerce_deps(state_or_deps)
    return {"ok": True, "observed": deps.get_observed_state()}


def get_consistency_report_model(state_or_deps: Any, *, auto_repair: bool = False) -> dict[str, Any]:
    """Return the consistency report wrapper used by the diagnostics route."""

    deps = _coerce_deps(state_or_deps)
    return {"ok": True, "report": deps.get_consistency_report(auto_repair=bool(auto_repair))}


def get_home_attention_level(observed: dict[str, Any] | None) -> str:
    """Map observed service state to the nav attention color used by the shell."""

    service_status = str((observed or {}).get("service_status_display", "") or "").strip().lower()
    if service_status == "crashed":
        return "red"
    if service_status in {"starting", "shutting down"}:
        return "yellow"
    if service_status == "running":
        return "green"
    return "none"
