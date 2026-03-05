"""Service status projection use cases."""


def get_service_status_display(ctx, service_status, players_online):
    intent = str(ctx.get_service_status_intent() or "").strip().lower()
    raw = str(service_status or "").strip().lower()
    if intent == "crashed":
        return "Crashed"
    if intent == "shutting":
        return "Shutting Down"
    if intent == "starting":
        if raw == "active":
            players_is_integer = isinstance(players_online, str) and players_online.isdigit()
            startup_ready = False
            try:
                startup_ready = bool(ctx.is_rcon_startup_ready(service_status=raw))
            except Exception:
                startup_ready = False
            if players_is_integer and startup_ready:
                ctx.set_service_status_intent(None)
                return "Running"
        return "Starting"
    if raw in {"inactive", "failed"}:
        ctx.set_service_status_intent(None)
        return "Off"
    if raw in {"deactivating", "shutting_down"}:
        return "Shutting Down"
    if raw in {"activating", "starting"}:
        return "Starting"
    if raw == "active":
        players_is_integer = isinstance(players_online, str) and players_online.isdigit()
        if players_is_integer:
            startup_ready = False
            try:
                startup_ready = bool(ctx.is_rcon_startup_ready(service_status=raw))
            except Exception:
                startup_ready = False
            if startup_ready:
                return "Running"
        return "Starting"
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
