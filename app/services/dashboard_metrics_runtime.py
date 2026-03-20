"""Dashboard metrics collection and publication helpers."""
from datetime import datetime
from pathlib import Path
import time
from typing import Any

from app.core import state_store as state_store_service
from app.services import client_registry as client_registry_service
from app.services import file_inventory_index as file_inventory_index_service
from app.services import maintenance_state_store as maintenance_state_store_service
from app.services import maintenance_scheduler as maintenance_scheduler_service
from app.services.dashboard_state_runtime import get_backups_status, get_observed_state
from app.services.worker_scheduler import WorkerSpec, start_worker


def mark_home_page_client_active(ctx: Any, client_id: str | None = None) -> None:
    """Record recent home-page activity and wake cadence workers."""
    if client_id:
        client_registry_service.touch_client(ctx, client_id, channel="home_heartbeat")
    with ctx.metrics_cache_cond:
        ctx.home_page_last_seen = time.time()
        ctx.metrics_cache_cond.notify_all()


def has_active_home_page_clients(ctx: Any) -> bool:
    """Return whether home-page activity is still within the active TTL."""
    with ctx.metrics_cache_cond:
        last_seen = float(getattr(ctx, "home_page_last_seen", 0.0) or 0.0)
        ttl_seconds = float(getattr(ctx, "HOME_PAGE_ACTIVE_TTL_SECONDS", 0.0) or 0.0)
    return (time.time() - last_seen) <= ttl_seconds


def has_active_flask_app_clients(ctx: Any) -> bool:
    """Return whether any shell page or SSE stream is actively consuming data."""
    now = time.time()
    with ctx.metrics_cache_cond:
        home_last_seen = float(getattr(ctx, "home_page_last_seen", 0.0) or 0.0)
        file_last_seen = float(getattr(ctx, "file_page_last_seen", 0.0) or 0.0)
        home_ttl_seconds = float(getattr(ctx, "HOME_PAGE_ACTIVE_TTL_SECONDS", 0.0) or 0.0)
        file_ttl_seconds = float(getattr(ctx, "FILE_PAGE_ACTIVE_TTL_SECONDS", 0.0) or 0.0)
    active_clients = client_registry_service.active_client_count(ctx)
    return (
        active_clients > 0
        or (now - home_last_seen) <= home_ttl_seconds
        or (now - file_last_seen) <= file_ttl_seconds
    )


def _maybe_refresh_idle_storage_cache(ctx: Any) -> bool:
    """Refresh storage-related cache when server is on but no clients are active."""
    try:
        service_status = ctx.get_status()
    except Exception:
        return False
    off_states = {str(item or "").strip().lower() for item in getattr(ctx, "OFF_STATES", {"inactive", "failed"})}
    if str(service_status or "").strip().lower() in off_states:
        return False
    now = time.time()
    last_at = float(getattr(ctx, "idle_storage_last_at", 0.0) or 0.0)
    interval = float(getattr(ctx, "METRICS_IDLE_STORAGE_REFRESH_SECONDS", 15.0) or 15.0)
    if (now - last_at) < interval:
        return False
    try:
        usage = ctx.get_storage_usage()
        backup_count, stale_worlds_count, backup_folder = _get_backup_and_stale_counts(ctx)
        cleanup_meta = maintenance_state_store_service.get_cleanup_meta(ctx, scope="backups")
        cleanup_missed_runs = maintenance_state_store_service.get_cleanup_missed_run_count(ctx)
        cleanup_next_run = maintenance_scheduler_service.get_next_cleanup_run_at(ctx, scope="backups")
    except Exception as exc:
        ctx.log_mcweb_exception("idle_storage_refresh", exc)
        ctx.idle_storage_last_at = now
        return False
    ctx.idle_storage_last_at = now
    ctx.idle_storage_usage_text = usage
    ctx.idle_storage_cache = {
        "storage_usage": usage,
        "backup_count": backup_count,
        "stale_worlds_count": stale_worlds_count,
        "backup_folder": backup_folder,
        "cleanup_meta": cleanup_meta,
        "cleanup_missed_runs": cleanup_missed_runs,
        "cleanup_next_run": cleanup_next_run,
    }
    # Update cached payload so reconnecting clients see fresh storage/cleanup data.
    with ctx.metrics_cache_cond:
        payload = dict(ctx.metrics_cache_payload) if isinstance(ctx.metrics_cache_payload, dict) else {}
        payload["storage_usage"] = usage
        payload["storage_usage_class"] = get_storage_usage_class(ctx, usage)
        payload["backup_files_count"] = backup_count
        payload["backup_folder"] = backup_folder
        payload["stale_worlds_count"] = stale_worlds_count
        payload["cleanup_last_run"] = cleanup_meta.get("last_run_at", "")
        payload["cleanup_rule_version"] = cleanup_meta.get("rule_version")
        payload["cleanup_schedule_version"] = cleanup_meta.get("schedule_version")
        payload["cleanup_last_changed_by"] = cleanup_meta.get("last_changed_by", "")
        payload["cleanup_missed_runs"] = cleanup_missed_runs
        payload["cleanup_next_run"] = cleanup_next_run
        system_group = dict(payload.get("system", {})) if isinstance(payload.get("system"), dict) else {}
        system_group["storage"] = usage
        payload["system"] = system_group
        backup_group = dict(payload.get("backup", {})) if isinstance(payload.get("backup"), dict) else {}
        backup_group["count"] = backup_count
        backup_group["folder"] = backup_folder
        payload["backup"] = backup_group
        cleanup_group = dict(payload.get("cleanup", {})) if isinstance(payload.get("cleanup"), dict) else {}
        cleanup_group["last_run"] = cleanup_meta.get("last_run_at", "")
        cleanup_group["rule_version"] = cleanup_meta.get("rule_version")
        cleanup_group["schedule_version"] = cleanup_meta.get("schedule_version")
        cleanup_group["last_changed_by"] = cleanup_meta.get("last_changed_by", "")
        cleanup_group["missed_runs"] = cleanup_missed_runs
        cleanup_group["next_run"] = cleanup_next_run
        cleanup_group["stale_worlds_count"] = stale_worlds_count
        payload["cleanup"] = cleanup_group
        ctx.metrics_cache_payload = payload
    return True


def class_from_percent(value: float) -> str:
    """Map a numeric percent to dashboard severity class."""
    if value < 60:
        return "stat-green"
    if value < 75:
        return "stat-yellow"
    if value < 90:
        return "stat-orange"
    return "stat-red"


def extract_percent(ctx: Any, usage_text: object) -> float | None:
    """Extract numeric percentage from human-readable usage text."""
    match = ctx.re.search(r"\(([\d.]+)%\)", usage_text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def usage_class_from_text(ctx: Any, usage_text: object) -> str:
    """Map usage text with percentage into dashboard severity class."""
    percent = extract_percent(ctx, usage_text)
    if percent is None:
        return "stat-red"
    return class_from_percent(percent)


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def get_cpu_per_core_items(ctx: Any, cpu_per_core: list[object]) -> list[dict[str, object]]:
    """Build per-core UI payload with normalized values/classes."""
    items: list[dict[str, object]] = []
    for i, raw in enumerate(cpu_per_core):
        val = _coerce_float(raw)
        if val is None:
            items.append({"index": i, "value": raw, "class": "stat-red"})
            continue
        items.append({"index": i, "value": f"{val:.1f}", "class": class_from_percent(val)})
    return items


def get_ram_usage_class(ctx: Any, ram_usage: object) -> str:
    """Classify RAM usage text."""
    return usage_class_from_text(ctx, ram_usage)


def get_storage_usage_class(ctx: Any, storage_usage: object) -> str:
    """Classify storage usage text."""
    return usage_class_from_text(ctx, storage_usage)


def get_cpu_frequency_class(ctx: Any, cpu_frequency: object) -> str:
    """Classify CPU frequency availability."""
    return "stat-red" if cpu_frequency == "unknown" else "stat-green"


def _players_is_int(players_online: object) -> bool:
    return str(players_online or "").strip().isdigit()


def _players_display(players_online: object) -> str:
    value = str(players_online or "").strip()
    return value if value.isdigit() else "-"


def _tick_display(tick_rate: object) -> str:
    value = str(tick_rate or "").strip()
    if not value:
        return "-"
    lowered = value.lower()
    if lowered in {"unknown", "--", "n/a", "none"}:
        return "-"
    if lowered.endswith("ms"):
        numeric = lowered[:-2].strip()
        try:
            float(numeric)
            return value
        except Exception:
            return "-"
    try:
        float(value)
        return value
    except Exception:
        return "-"


def _get_backup_and_stale_counts(ctx: Any) -> tuple[int, int, str]:
    backup_count = 0
    stale_worlds_count = 0
    try:
        backup_dir = Path(ctx.BACKUP_DIR)
    except Exception:
        return 0, 0, ""
    snapshot_root = Path(getattr(ctx, "AUTO_SNAPSHOT_DIR", "") or (backup_dir / "snapshots"))
    session_state = getattr(ctx, "session_state", None)
    session_file_text = str(getattr(session_state, "session_file", "") or "").strip() if session_state is not None else ""
    session_file = Path(session_file_text) if session_file_text else None
    old_worlds_root = (session_file.parent / "old_worlds").resolve() if session_file is not None else Path("__unused_old_worlds_index_root__")
    try:
        inventory = file_inventory_index_service.get_inventory(
            backup_root=backup_dir,
            snapshot_root=snapshot_root,
            old_worlds_root=old_worlds_root,
        )
    except Exception:
        return 0, 0, str(backup_dir)
    backup_count = len(inventory.get("backup_zip_paths", [])) + len(inventory.get("snapshot_dir_paths", []))
    stale_worlds_count = sum(1 for entry in inventory.get("old_world_top_entries", []) if entry.is_dir())
    return backup_count, stale_worlds_count, str(backup_dir)


def _resolve_service_status_display(
    ctx: Any,
    service_status: object,
    players_online: object,
    tick_rate: object,
    observed_display: object,
) -> str:
    raw = str(service_status or "").strip().lower()
    intent = ""
    try:
        intent = str(ctx.get_service_status_intent() or "").strip().lower()
    except Exception:
        intent = ""

    if intent == "crashed":
        return "Crashed"
    off_states = getattr(ctx, "OFF_STATES", {"inactive", "failed"})
    if raw in off_states:
        return "Off"
    if raw in {"activating", "starting"}:
        return "Starting"
    if raw in {"deactivating", "shutting_down"}:
        return "Shutting Down"
    if raw != "active":
        observed = str(observed_display or "").strip()
        return observed or "Off"

    if intent == "shutting":
        return "Shutting Down"
    players_known = _players_is_int(players_online)
    if players_known:
        return "Running"
    return "Starting"


def slow_metrics_ttl_seconds(ctx: Any, service_status: object, *, active_clients: bool = False) -> float:
    """Return slow-metric cache TTL for current service state."""
    if active_clients:
        try:
            collect_interval = float(getattr(ctx, "METRICS_COLLECT_INTERVAL_SECONDS", 1.0) or 1.0)
        except Exception:
            collect_interval = 1.0
        try:
            configured = float(getattr(ctx, "SLOW_METRICS_INTERVAL_ACTIVE_SECONDS", collect_interval) or collect_interval)
        except Exception:
            configured = collect_interval
        return min(collect_interval, configured)
    if service_status == "active":
        return float(getattr(ctx, "SLOW_METRICS_INTERVAL_ACTIVE_SECONDS", 5.0) or 5.0)
    return float(getattr(ctx, "SLOW_METRICS_INTERVAL_OFF_SECONDS", 15.0) or 15.0)


def get_slow_metrics(ctx: Any, service_status: object, *, active_clients: bool = False) -> dict[str, Any]:
    """Return cached slow metrics or refresh when TTL expires."""
    now = time.time()
    ttl = slow_metrics_ttl_seconds(ctx, service_status, active_clients=active_clients)
    with ctx.slow_metrics_lock:
        if (
            ctx.slow_metrics_cache
            and ctx.slow_metrics_cache_status == service_status
            and (now - ctx.slow_metrics_cache_at) <= ttl
        ):
            return dict(ctx.slow_metrics_cache)

    snapshot: dict[str, Any] = {
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


def collect_dashboard_metrics(ctx: Any) -> dict[str, Any]:
    """Collect one full dashboard metrics snapshot."""
    active_clients = has_active_flask_app_clients(ctx)
    observed = get_observed_state(ctx)
    service_status = str(observed.get("service_status_raw", "") or ctx.get_status())
    slow = get_slow_metrics(ctx, service_status, active_clients=active_clients)
    cpu_per_core = slow["cpu_per_core"]
    ram_usage = slow["ram_usage"]
    cpu_frequency = slow["cpu_frequency"]
    storage_usage = slow["storage_usage"]
    low_storage_blocked = ctx.is_storage_low(storage_usage)
    players_online_raw = None
    tick_rate_raw = None
    probe_fn = getattr(ctx, "_probe_minecraft_runtime_metrics", None)
    if callable(probe_fn):
        try:
            players_online_raw, tick_rate_raw = probe_fn(force=active_clients)
        except Exception:
            players_online_raw = None
            tick_rate_raw = None
    if players_online_raw is None:
        players_online_raw = observed.get("players_online", ctx.get_players_online())
    players_online = _players_display(players_online_raw)
    if tick_rate_raw is None:
        tick_rate_raw = ctx.get_tick_rate()
    tick_rate = _tick_display(tick_rate_raw)
    session_duration = ctx.get_session_duration_text()
    # Keep home card status aligned with nav-attention source of truth.
    service_status_display = str(observed.get("service_status_display", "") or "").strip()
    if not service_status_display:
        service_status_display = _resolve_service_status_display(
            ctx,
            service_status,
            players_online,
            tick_rate,
            observed.get("service_status_display", ""),
        )
    backup_schedule = ctx.get_backup_schedule_times(service_status)
    backup_status, backup_status_class = ctx.get_backup_status()
    backup_warning = ctx.get_backup_warning_state(ctx.BACKUP_WARNING_TTL_SECONDS)
    now_display = datetime.now(tz=ctx.DISPLAY_TZ)
    server_time_text = now_display.strftime("%b %d, %Y %I:%M:%S %p %Z")
    server_time_epoch_ms = int(now_display.timestamp() * 1000)
    server_time_zone = str(now_display.tzname() or "").strip()
    backup_count, stale_worlds_count, backup_folder = _get_backup_and_stale_counts(ctx)
    cleanup_meta = maintenance_state_store_service.get_cleanup_meta(ctx, scope="backups")
    cleanup_missed_runs = maintenance_state_store_service.get_cleanup_missed_run_count(ctx)
    cleanup_next_run = maintenance_scheduler_service.get_next_cleanup_run_at(ctx, scope="backups")

    is_running_display = str(service_status_display or "").strip().lower() == "running"
    idle_countdown = "--:--"
    if is_running_display and str(players_online_raw or "").strip() == "0":
        idle_countdown = ctx.get_idle_countdown("active", "0")

    cpu_per_core_items = get_cpu_per_core_items(ctx, cpu_per_core)
    return {
        "system": {
            "ram": ram_usage,
            "cpu": cpu_per_core_items,
            "freq": cpu_frequency,
            "storage": storage_usage,
        },
        "minecraft": {
            "status": service_status_display,
            "players": players_online,
            "tick_time": tick_rate,
            "auto_stop": idle_countdown,
        },
        "backup": {
            "status": backup_status,
            "last": backup_schedule["last_backup_time"],
            "next": backup_schedule["next_backup_time"],
            "count": backup_count,
            "folder": backup_folder,
        },
        "cleanup": {
            "last_run": cleanup_meta.get("last_run_at", ""),
            "rule_version": cleanup_meta.get("rule_version"),
            "schedule_version": cleanup_meta.get("schedule_version"),
            "last_changed_by": cleanup_meta.get("last_changed_by", ""),
            "missed_runs": cleanup_missed_runs,
            "next_run": cleanup_next_run,
            "stale_worlds_count": stale_worlds_count,
        },
        "service_status": service_status_display,
        "service_status_class": ctx.get_service_status_class(service_status_display),
        "service_running_status": service_status,
        "backups_status": slow["backups_status"],
        "ram_usage": ram_usage,
        "ram_usage_class": get_ram_usage_class(ctx, ram_usage),
        "cpu_per_core_items": cpu_per_core_items,
        "cpu_frequency": cpu_frequency,
        "cpu_frequency_class": get_cpu_frequency_class(ctx, cpu_frequency),
        "storage_usage": storage_usage,
        "storage_usage_class": get_storage_usage_class(ctx, storage_usage),
        "low_storage_blocked": low_storage_blocked,
        "low_storage_message": ctx.low_storage_error_message(storage_usage) if low_storage_blocked else "",
        "players_online": players_online,
        "tick_rate": tick_rate,
        "session_duration": session_duration,
        "idle_countdown": idle_countdown,
        "backup_status": backup_status,
        "backup_status_class": backup_status_class,
        "backup_warning_seq": int(backup_warning.get("seq", 0) or 0),
        "backup_warning_message": str(backup_warning.get("message", "") or ""),
        "last_backup_time": backup_schedule["last_backup_time"],
        "next_backup_time": backup_schedule["next_backup_time"],
        "backup_files_count": backup_count,
        "backup_folder": backup_folder,
        "stale_worlds_count": stale_worlds_count,
        "cleanup_last_run": cleanup_meta.get("last_run_at", ""),
        "cleanup_rule_version": cleanup_meta.get("rule_version"),
        "cleanup_schedule_version": cleanup_meta.get("schedule_version"),
        "cleanup_last_changed_by": cleanup_meta.get("last_changed_by", ""),
        "cleanup_missed_runs": cleanup_missed_runs,
        "cleanup_next_run": cleanup_next_run,
        "server_time": server_time_text,
        "server_time_epoch_ms": server_time_epoch_ms,
        "server_time_zone": server_time_zone,
        "world_name": ctx.get_world_name(),
        "rcon_enabled": ctx.is_rcon_enabled(),
        "observed_state": observed,
    }


def publish_metrics_snapshot(ctx: Any, snapshot: dict[str, Any] | None) -> None:
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


def _metrics_interval_seconds(ctx: Any, snapshot: dict[str, Any] | None) -> float:
    if has_active_flask_app_clients(ctx):
        return float(getattr(ctx, "METRICS_COLLECT_INTERVAL_SECONDS", 1.0) or 1.0)
    service_status = str((snapshot or {}).get("service_running_status", "") or "").strip().lower()
    if service_status == "active":
        return float(
            getattr(
                ctx,
                "SLOW_METRICS_INTERVAL_ACTIVE_SECONDS",
                getattr(ctx, "METRICS_COLLECT_INTERVAL_SECONDS", 1.0),
            )
            or 1.0
        )
    return float(
        getattr(
            ctx,
            "SLOW_METRICS_INTERVAL_OFF_SECONDS",
            getattr(ctx, "METRICS_COLLECT_INTERVAL_OFF_SECONDS", 15.0),
        )
        or 15.0
    )


def collect_and_publish_metrics(ctx: Any) -> dict[str, Any] | None:
    """Collect dashboard metrics and publish them to cache/streams."""
    try:
        snapshot = collect_dashboard_metrics(ctx)
    except Exception as exc:
        ctx.log_mcweb_exception("metrics_collect", exc)
        return None
    publish_metrics_snapshot(ctx, snapshot)
    return snapshot


def metrics_collector_loop(ctx: Any) -> None:
    """Background metrics loop that idles when there are no consumers."""
    process_role = str(getattr(ctx, "PROCESS_ROLE", "all") or "all").strip().lower()
    always_collect = process_role == "worker"
    while True:
        if not always_collect:
            with ctx.metrics_cache_cond:
                # Wait until either SSE consumers exist or the page heartbeat is active.
                ctx.metrics_cache_cond.wait_for(
                    lambda: has_active_flask_app_clients(ctx),
                    timeout=1,
                )
                should_collect = has_active_flask_app_clients(ctx)
            if not should_collect:
                _maybe_refresh_idle_storage_cache(ctx)
                continue
        snapshot = collect_and_publish_metrics(ctx)
        interval = _metrics_interval_seconds(ctx, snapshot)
        with ctx.metrics_cache_cond:
            if always_collect or has_active_flask_app_clients(ctx):
                ctx.metrics_cache_cond.wait(timeout=interval)


def ensure_metrics_collector_started(ctx: Any) -> None:
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


def get_cached_dashboard_metrics(ctx: Any) -> dict[str, Any]:
    """Return last metrics snapshot, or a safe default payload."""
    with ctx.metrics_cache_cond:
        if ctx.metrics_cache_payload:
            return dict(ctx.metrics_cache_payload)
    now_display = datetime.now(tz=ctx.DISPLAY_TZ)
    server_time_text = now_display.strftime("%b %d, %Y %I:%M:%S %p %Z")
    server_time_epoch_ms = int(now_display.timestamp() * 1000)
    server_time_zone = str(now_display.tzname() or "").strip()
    return {
        "system": {
            "ram": "unknown",
            "cpu": [{"index": 0, "value": "unknown", "class": "stat-red"}],
            "freq": "unknown",
            "storage": "unknown",
        },
        "minecraft": {
            "status": "Off",
            "players": "unknown",
            "tick_time": "unknown",
            "auto_stop": "--:--",
        },
        "backup": {
            "status": "Idle",
            "last": "--",
            "next": "--",
            "count": 0,
            "folder": str(getattr(ctx, "BACKUP_DIR", "") or ""),
        },
        "cleanup": {
            "last_run": "",
            "rule_version": None,
            "schedule_version": None,
            "last_changed_by": "",
            "missed_runs": 0,
            "next_run": "",
            "stale_worlds_count": 0,
        },
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

