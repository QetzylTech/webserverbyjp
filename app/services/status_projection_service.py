"""Service status projection use cases."""

import time


def get_service_status_display(ctx, service_status, players_online):
    intent = ctx.get_service_status_intent()
    if intent == "crashed":
        return "Crashed"
    if service_status in ("inactive", "failed"):
        ctx.set_service_status_intent(None)
        return "Off"
    if service_status == "activating":
        return "Starting"
    if service_status == "deactivating":
        return "Shutting Down"
    if service_status == "active":
        if intent == "shutting":
            return "Shutting Down"
        if intent == "starting":
            startup_grace_seconds = 45
            try:
                started_at = ctx.read_session_start_time()
            except Exception:
                started_at = None
            if started_at is not None and (time.time() - float(started_at)) < startup_grace_seconds:
                return "Starting"
            ctx.set_service_status_intent(None)
        players_is_integer = isinstance(players_online, str) and players_online.isdigit()
        if players_is_integer:
            return "Running"
        return "Running"
    return "Off"


def get_service_status_class(service_status_display):
    if service_status_display == "Running":
        return "stat-green"
    if service_status_display == "Starting":
        return "stat-yellow"
    if service_status_display == "Shutting Down":
        return "stat-orange"
    if service_status_display == "Crashed":
        return "stat-red"
    return "stat-red"
