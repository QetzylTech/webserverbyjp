"""Dashboard metrics aggregation and publication services."""

from datetime import datetime
import time

from app.core import state_store as state_store_service
from app.services.dashboard_query_service import get_backups_status, get_observed_state
from app.services.worker_scheduler import WorkerSpec, start_worker

def class_from_percent(value):
    """Map a numeric percent to dashboard severity class."""
    if value < 60:
        return "stat-green"
    if value < 75:
        return "stat-yellow"
    if value < 90:
        return "stat-orange"
    return "stat-red"


def extract_percent(ctx, usage_text):
    """Extract numeric percentage from human-readable usage text."""
    match = ctx.re.search(r"\(([\d.]+)%\)", usage_text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def usage_class_from_text(ctx, usage_text):
    """Map usage text with percentage into dashboard severity class."""
    percent = extract_percent(ctx, usage_text)
    if percent is None:
        return "stat-red"
    return class_from_percent(percent)


def get_cpu_per_core_items(ctx, cpu_per_core):
    """Build per-core UI payload with normalized values/classes."""
    items = []
    for i, raw in enumerate(cpu_per_core):
        try:
            val = float(raw)
        except ValueError:
            items.append({"index": i, "value": raw, "class": "stat-red"})
            continue
        items.append({"index": i, "value": f"{val:.1f}", "class": class_from_percent(val)})
    return items


def get_ram_usage_class(ctx, ram_usage):
    """Classify RAM usage text."""
    return usage_class_from_text(ctx, ram_usage)


def get_storage_usage_class(ctx, storage_usage):
    """Classify storage usage text."""
    return usage_class_from_text(ctx, storage_usage)


def get_cpu_frequency_class(ctx, cpu_frequency):
    """Classify CPU frequency availability."""
    return "stat-red" if cpu_frequency == "unknown" else "stat-green"


def slow_metrics_ttl_seconds(ctx, service_status):
    """Return slow-metric cache TTL for current service state."""
    if service_status == "active":
        return ctx.SLOW_METRICS_INTERVAL_ACTIVE_SECONDS
    return ctx.SLOW_METRICS_INTERVAL_OFF_SECONDS


def get_slow_metrics(ctx, service_status):
    """Return cached slow metrics or refresh when TTL expires."""
    now = time.time()
    ttl = slow_metrics_ttl_seconds(ctx, service_status)
    with ctx.slow_metrics_lock:
        if (
            ctx.slow_metrics_cache
            and ctx.slow_metrics_cache_status == service_status
            and (now - ctx.slow_metrics_cache_at) <= ttl
        ):
            return dict(ctx.slow_metrics_cache)

    snapshot = {
        "cpu_per_core": ctx.get_cpu_usage_per_core(),
        "ram_usage": ctx.get_ram_usage(),
        "cpu_frequency": ctx.get_cpu_frequency(),
        "storage_usage": ctx.get_storage_usage(),
        "backups_status": get_backups_status(ctx),
    }
    with ctx.slow_metrics_lock:
        ctx.slow_metrics_cache = dict(snapshot)
        ctx.slow_metrics_cache_status = service_status
        ctx.slow_metrics_cache_at = now
    return snapshot


def collect_dashboard_metrics(ctx):
    """Collect one full dashboard metrics snapshot."""
    observed = get_observed_state(ctx)
    service_status = str(observed.get("service_status_raw", "") or ctx.get_status())
    slow = get_slow_metrics(ctx, service_status)
    cpu_per_core = slow["cpu_per_core"]
    ram_usage = slow["ram_usage"]
    cpu_frequency = slow["cpu_frequency"]
    storage_usage = slow["storage_usage"]
    low_storage_blocked = ctx.is_storage_low(storage_usage)
    players_online = observed.get("players_online", ctx.get_players_online())
    tick_rate = ctx.get_tick_rate()
    session_duration = ctx.get_session_duration_text()
    service_status_display = str(observed.get("service_status_display", "") or ctx.get_service_status_display(service_status, players_online))
    backup_schedule = ctx.get_backup_schedule_times(service_status)
    backup_status, backup_status_class = ctx.get_backup_status()
    backup_warning = ctx.get_backup_warning_state(ctx.BACKUP_WARNING_TTL_SECONDS)
    now_display = datetime.now(tz=ctx.DISPLAY_TZ)
    server_time_text = now_display.strftime("%b %d, %Y %I:%M:%S %p %Z")
    server_time_epoch_ms = int(now_display.timestamp() * 1000)
    server_time_zone = str(now_display.tzname() or "").strip()

    return {
        "service_status": service_status_display,
        "service_status_class": ctx.get_service_status_class(service_status_display),
        "service_running_status": service_status,
        "backups_status": slow["backups_status"],
        "ram_usage": ram_usage,
        "ram_usage_class": get_ram_usage_class(ctx, ram_usage),
        "cpu_per_core_items": get_cpu_per_core_items(ctx, cpu_per_core),
        "cpu_frequency": cpu_frequency,
        "cpu_frequency_class": get_cpu_frequency_class(ctx, cpu_frequency),
        "storage_usage": storage_usage,
        "storage_usage_class": get_storage_usage_class(ctx, storage_usage),
        "low_storage_blocked": low_storage_blocked,
        "low_storage_message": ctx.low_storage_error_message(storage_usage) if low_storage_blocked else "",
        "players_online": players_online,
        "tick_rate": tick_rate,
        "session_duration": session_duration,
        "idle_countdown": ctx.get_idle_countdown(service_status, players_online),
        "backup_status": backup_status,
        "backup_status_class": backup_status_class,
        "backup_warning_seq": int(backup_warning.get("seq", 0) or 0),
        "backup_warning_message": str(backup_warning.get("message", "") or ""),
        "last_backup_time": backup_schedule["last_backup_time"],
        "next_backup_time": backup_schedule["next_backup_time"],
        "server_time": server_time_text,
        "server_time_epoch_ms": server_time_epoch_ms,
        "server_time_zone": server_time_zone,
        "world_name": ctx.get_world_name(),
        "rcon_enabled": ctx.is_rcon_enabled(),
        "observed_state": observed,
    }


def publish_metrics_snapshot(ctx, snapshot):
    """Publish latest metrics snapshot to all stream listeners."""
    event_id = 0
    db_path = getattr(ctx, "APP_STATE_DB_PATH", None)
    if db_path is not None:
        try:
            event_id = int(
                state_store_service.append_event(
                    db_path,
                    topic="metrics_snapshot",
                    payload={"snapshot": dict(snapshot) if isinstance(snapshot, dict) else {}},
                )
                or 0
            )
        except Exception:
            event_id = 0
    with ctx.metrics_cache_cond:
        ctx.metrics_cache_payload = snapshot
        ctx.metrics_cache_seq = int(event_id or (ctx.metrics_cache_seq + 1))
        ctx.metrics_cache_cond.notify_all()


def mark_home_page_client_active(ctx):
    """Record recent home-page activity for metrics throttling."""
    with ctx.metrics_cache_cond:
        ctx.home_page_last_seen = time.time()
        ctx.metrics_cache_cond.notify_all()


def has_active_home_page_clients(ctx):
    """Return whether home-page activity is still within active TTL."""
    with ctx.metrics_cache_cond:
        last_seen = ctx.home_page_last_seen
    return (time.time() - last_seen) <= ctx.HOME_PAGE_ACTIVE_TTL_SECONDS


def collect_and_publish_metrics(ctx):
    """Collect dashboard metrics and publish them to cache/streams."""
    try:
        snapshot = collect_dashboard_metrics(ctx)
    except Exception as exc:
        ctx.log_mcweb_exception("metrics_collect", exc)
        return False
    publish_metrics_snapshot(ctx, snapshot)
    return True


def metrics_collector_loop(ctx):
    """Background metrics loop that idles when there are no consumers."""
    process_role = str(getattr(ctx, "PROCESS_ROLE", "all") or "all").strip().lower()
    always_collect = process_role == "worker"
    while True:
        if not always_collect:
            with ctx.metrics_cache_cond:
                # Wait until either SSE consumers exist or the page heartbeat is active.
                ctx.metrics_cache_cond.wait_for(
                    lambda: ctx.metrics_stream_client_count > 0 or has_active_home_page_clients(ctx),
                    timeout=1,
                )
                should_collect = ctx.metrics_stream_client_count > 0 or has_active_home_page_clients(ctx)
            if not should_collect:
                continue
        collect_and_publish_metrics(ctx)
        service_status = ctx.get_status()
        interval = ctx.METRICS_COLLECT_INTERVAL_SECONDS if service_status == "active" else ctx.METRICS_COLLECT_INTERVAL_OFF_SECONDS
        with ctx.metrics_cache_cond:
            if always_collect or ctx.metrics_stream_client_count > 0 or has_active_home_page_clients(ctx):
                ctx.metrics_cache_cond.wait(timeout=interval)


def ensure_metrics_collector_started(ctx):
    """Start metrics collector daemon once."""
    if ctx.metrics_collector_started:
        return
    with ctx.metrics_collector_start_lock:
        if ctx.metrics_collector_started:
            return
        start_worker(
            ctx,
            WorkerSpec(
                name="metrics-collector",
                target=metrics_collector_loop,
                args=(ctx,),
                interval_source=getattr(ctx, "METRICS_COLLECT_INTERVAL_SECONDS", None),
                stop_signal_name="metrics_collector_stop_event",
                health_marker="metrics_collector",
            ),
        )
        ctx.metrics_collector_started = True


def get_cached_dashboard_metrics(ctx):
    """Return last metrics snapshot, or a safe default payload."""
    with ctx.metrics_cache_cond:
        if ctx.metrics_cache_payload:
            return dict(ctx.metrics_cache_payload)
    now_display = datetime.now(tz=ctx.DISPLAY_TZ)
    server_time_text = now_display.strftime("%b %d, %Y %I:%M:%S %p %Z")
    server_time_epoch_ms = int(now_display.timestamp() * 1000)
    server_time_zone = str(now_display.tzname() or "").strip()
    return {
        "service_status": "Off",
        "service_status_class": "stat-red",
        "service_running_status": "inactive",
        "backups_status": "unknown",
        "ram_usage": "unknown",
        "ram_usage_class": "stat-red",
        "cpu_per_core_items": [{"index": 0, "value": "unknown", "class": "stat-red"}],
        "cpu_frequency": "unknown",
        "cpu_frequency_class": "stat-red",
        "storage_usage": "unknown",
        "storage_usage_class": "stat-red",
        "low_storage_blocked": False,
        "low_storage_message": "",
        "players_online": "unknown",
        "tick_rate": "unknown",
        "session_duration": "--",
        "idle_countdown": "--:--",
        "backup_status": "Idle",
        "backup_status_class": "stat-yellow",
        "backup_warning_seq": 0,
        "backup_warning_message": "",
        "last_backup_time": "--",
        "next_backup_time": "--",
        "server_time": server_time_text,
        "server_time_epoch_ms": server_time_epoch_ms,
        "server_time_zone": server_time_zone,
        "world_name": ctx.get_world_name(),
        "rcon_enabled": ctx.is_rcon_enabled(),
    }

