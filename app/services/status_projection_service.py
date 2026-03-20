"""Service status projection use cases."""

from __future__ import annotations

from typing import Any


def _players_known(players_online: Any) -> bool:
    return isinstance(players_online, str) and players_online.isdigit()


def _startup_ready(ctx: Any, raw_status: str, players_online: Any) -> bool:
    if raw_status != "active" or not _players_known(players_online):
        return False
    try:
        return bool(ctx.is_rcon_startup_ready(service_status=raw_status))
    except Exception:
        return False


def get_service_status_display(ctx: Any, service_status: Any, players_online: Any) -> str:
    intent = str(ctx.get_service_status_intent() or "").strip().lower()
    raw = str(service_status or "").strip().lower()
    off_states = {str(item or "").strip().lower() for item in getattr(ctx, "OFF_STATES", {"inactive", "failed"})}

    if intent == "crashed":
        return "Crashed"
    if raw in off_states:
        ctx.set_service_status_intent(None)
        return "Off"
    if raw in {"deactivating", "shutting_down"} or intent == "shutting":
        return "Shutting Down"
    if raw in {"activating", "starting"} or intent == "starting":
        if _startup_ready(ctx, raw, players_online):
            ctx.set_service_status_intent(None)
            return "Running"
        return "Starting"
    if raw == "active":
        if _startup_ready(ctx, raw, players_online):
            return "Running"
        return "Starting"

    ctx.set_service_status_intent(None)
    return "Off"


def get_service_status_class(service_status_display: str) -> str:
    if service_status_display == "Running":
        return "stat-green"
    if service_status_display == "Starting":
        return "stat-yellow"
    if service_status_display == "Shutting Down":
        return "stat-orange"
    if service_status_display == "Crashed":
        return "stat-red"
    return "stat-red"
