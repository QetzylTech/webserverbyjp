"""Dashboard runtime caching, metrics, and file-list services."""

import subprocess
import threading
import time


def load_backup_log_cache_from_disk(ctx):
    """Reload backup log cache from disk into bounded in-memory storage."""
    lines = ctx._read_recent_file_lines(ctx.BACKUP_LOG_FILE, 200)
    mtime_ns = ctx._safe_file_mtime_ns(ctx.BACKUP_LOG_FILE)
    with ctx.backup_log_cache_lock:
        ctx.backup_log_cache_lines.clear()
        ctx.backup_log_cache_lines.extend(lines)
        ctx.backup_log_cache_loaded = True
        ctx.backup_log_cache_mtime_ns = mtime_ns


def append_backup_log_cache_line(ctx, line):
    """Append one backup log line into cache, updating file mtime hint."""
    clean = (line or "").rstrip("\r\n")
    if not clean:
        return
    with ctx.backup_log_cache_lock:
        ctx.backup_log_cache_lines.append(clean)
        ctx.backup_log_cache_loaded = True
        ctx.backup_log_cache_mtime_ns = ctx._safe_file_mtime_ns(ctx.BACKUP_LOG_FILE)


def get_cached_backup_log_text(ctx):
    """Return backup log text, reloading only when on-disk mtime changes."""
    current_mtime_ns = ctx._safe_file_mtime_ns(ctx.BACKUP_LOG_FILE)
    with ctx.backup_log_cache_lock:
        loaded = ctx.backup_log_cache_loaded
        cached_mtime_ns = ctx.backup_log_cache_mtime_ns
        if loaded and cached_mtime_ns == current_mtime_ns:
            return "\n".join(ctx.backup_log_cache_lines).strip() or "(no logs)"
    load_backup_log_cache_from_disk(ctx)
    with ctx.backup_log_cache_lock:
        return "\n".join(ctx.backup_log_cache_lines).strip() or "(no logs)"


def load_minecraft_log_cache_from_journal(ctx):
    """Prime minecraft log cache from recent journalctl output."""
    result = subprocess.run(
        ["journalctl", "-u", ctx.SERVICE, "-n", "1000", "--no-pager"],
        capture_output=True,
        text=True,
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    lines = output.splitlines() if output else []
    with ctx.minecraft_log_cache_lock:
        ctx.minecraft_log_cache_lines.clear()
        ctx.minecraft_log_cache_lines.extend(lines)
        ctx.minecraft_log_cache_loaded = True


def append_minecraft_log_cache_line(ctx, line):
    """Append one minecraft journal line into cache."""
    clean = (line or "").rstrip("\r\n")
    if not clean:
        return
    with ctx.minecraft_log_cache_lock:
        ctx.minecraft_log_cache_lines.append(clean)
        ctx.minecraft_log_cache_loaded = True


def get_cached_minecraft_log_text(ctx):
    """Return minecraft log cache, loading initial snapshot on demand."""
    with ctx.minecraft_log_cache_lock:
        if ctx.minecraft_log_cache_loaded:
            return "\n".join(ctx.minecraft_log_cache_lines).strip() or "(no logs)"
    load_minecraft_log_cache_from_journal(ctx)
    with ctx.minecraft_log_cache_lock:
        return "\n".join(ctx.minecraft_log_cache_lines).strip() or "(no logs)"


def load_mcweb_log_cache_from_disk(ctx):
    """Reload mcweb action log cache from disk."""
    lines = ctx._read_recent_file_lines(ctx.MCWEB_ACTION_LOG_FILE, 200)
    mtime_ns = ctx._safe_file_mtime_ns(ctx.MCWEB_ACTION_LOG_FILE)
    with ctx.mcweb_log_cache_lock:
        ctx.mcweb_log_cache_lines.clear()
        ctx.mcweb_log_cache_lines.extend(lines)
        ctx.mcweb_log_cache_loaded = True
        ctx.mcweb_log_cache_mtime_ns = mtime_ns


def append_mcweb_log_cache_line(ctx, line):
    """Append one mcweb action log line into cache."""
    clean = (line or "").rstrip("\r\n")
    if not clean:
        return
    with ctx.mcweb_log_cache_lock:
        ctx.mcweb_log_cache_lines.append(clean)
        ctx.mcweb_log_cache_loaded = True
        ctx.mcweb_log_cache_mtime_ns = ctx._safe_file_mtime_ns(ctx.MCWEB_ACTION_LOG_FILE)


def get_cached_mcweb_log_text(ctx):
    """Return mcweb action log text, refreshing if file changed."""
    current_mtime_ns = ctx._safe_file_mtime_ns(ctx.MCWEB_ACTION_LOG_FILE)
    with ctx.mcweb_log_cache_lock:
        loaded = ctx.mcweb_log_cache_loaded
        cached_mtime_ns = ctx.mcweb_log_cache_mtime_ns
        if loaded and cached_mtime_ns == current_mtime_ns:
            return "\n".join(ctx.mcweb_log_cache_lines).strip() or "(no logs)"
    load_mcweb_log_cache_from_disk(ctx)
    with ctx.mcweb_log_cache_lock:
        return "\n".join(ctx.mcweb_log_cache_lines).strip() or "(no logs)"


def set_file_page_items(ctx, cache_key, items):
    """Replace cached file-list payload for one page section."""
    with ctx.file_page_cache_lock:
        ctx.file_page_cache[cache_key] = {
            "items": [dict(item) for item in items],
            "updated_at": time.time(),
        }


def refresh_file_page_items(ctx, cache_key):
    """Refresh one file-list cache key from its backing directory."""
    if cache_key == "backups":
        items = ctx._list_download_files(ctx.BACKUP_DIR, "*.zip", ctx.DISPLAY_TZ)
    elif cache_key == "crash_logs":
        items = ctx._list_download_files(ctx.CRASH_REPORTS_DIR, "*.txt", ctx.DISPLAY_TZ)
    elif cache_key == "minecraft_logs":
        items = ctx._list_download_files(ctx.MINECRAFT_LOGS_DIR, "*.log", ctx.DISPLAY_TZ)
        items.extend(ctx._list_download_files(ctx.MINECRAFT_LOGS_DIR, "*.gz", ctx.DISPLAY_TZ))
        items.sort(key=lambda item: item["mtime"], reverse=True)
    else:
        return []
    set_file_page_items(ctx, cache_key, items)
    return items


def mark_file_page_client_active(ctx):
    """Record recent file-page activity for refresher throttling."""
    with ctx.file_page_cache_lock:
        ctx.file_page_last_seen = time.time()


def has_active_file_page_clients(ctx):
    """Return whether file-page clients are still considered active."""
    with ctx.file_page_cache_lock:
        last_seen = ctx.file_page_last_seen
    return (time.time() - last_seen) <= ctx.FILE_PAGE_ACTIVE_TTL_SECONDS


def get_cached_file_page_items(ctx, cache_key):
    """Return cached file-list items when fresh; otherwise refresh."""
    with ctx.file_page_cache_lock:
        entry = ctx.file_page_cache.get(cache_key)
        if entry:
            age = time.time() - entry["updated_at"]
            if entry["items"] and age <= ctx.FILE_PAGE_CACHE_REFRESH_SECONDS:
                return [dict(item) for item in entry["items"]]
    return refresh_file_page_items(ctx, cache_key)


def file_page_cache_refresher_loop(ctx):
    """Background refresher that updates file lists only when viewed."""
    while True:
        if has_active_file_page_clients(ctx):
            for cache_key in ("backups", "crash_logs", "minecraft_logs"):
                try:
                    refresh_file_page_items(ctx, cache_key)
                except Exception as exc:
                    ctx.log_mcweb_exception(f"file_page_cache_refresh/{cache_key}", exc)
            time.sleep(ctx.FILE_PAGE_CACHE_REFRESH_SECONDS)
        else:
            time.sleep(1)


def ensure_file_page_cache_refresher_started(ctx):
    """Start file-page refresher daemon once."""
    if ctx.file_page_cache_refresher_started:
        return
    with ctx.file_page_cache_refresher_start_lock:
        if ctx.file_page_cache_refresher_started:
            return
        watcher = threading.Thread(target=file_page_cache_refresher_loop, args=(ctx,), daemon=True)
        watcher.start()
        ctx.file_page_cache_refresher_started = True


def get_backups_status(ctx):
    """Return backup directory health and current zip count summary."""
    if not ctx.BACKUP_DIR.exists() or not ctx.BACKUP_DIR.is_dir():
        return "missing"
    zip_count = sum(1 for _ in ctx.BACKUP_DIR.glob("*.zip"))
    return f"ready ({zip_count} zip files)"


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
    service_status = ctx.get_status()
    slow = get_slow_metrics(ctx, service_status)
    cpu_per_core = slow["cpu_per_core"]
    ram_usage = slow["ram_usage"]
    cpu_frequency = slow["cpu_frequency"]
    storage_usage = slow["storage_usage"]
    players_online = ctx.get_players_online()
    tick_rate = ctx.get_tick_rate()
    session_duration = ctx.get_session_duration_text()
    service_status_display = ctx.get_service_status_display(service_status, players_online)
    backup_schedule = ctx.get_backup_schedule_times(service_status)
    backup_status, backup_status_class = ctx.get_backup_status()

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
        "players_online": players_online,
        "tick_rate": tick_rate,
        "session_duration": session_duration,
        "idle_countdown": ctx.get_idle_countdown(service_status, players_online),
        "backup_status": backup_status,
        "backup_status_class": backup_status_class,
        "last_backup_time": backup_schedule["last_backup_time"],
        "next_backup_time": backup_schedule["next_backup_time"],
        "server_time": ctx.get_server_time_text(),
        "rcon_enabled": ctx.is_rcon_enabled(),
    }


def publish_metrics_snapshot(ctx, snapshot):
    """Publish latest metrics snapshot to all stream listeners."""
    with ctx.metrics_cache_cond:
        ctx.metrics_cache_payload = snapshot
        ctx.metrics_cache_seq += 1
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
    while True:
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
            if ctx.metrics_stream_client_count > 0 or has_active_home_page_clients(ctx):
                ctx.metrics_cache_cond.wait(timeout=interval)


def ensure_metrics_collector_started(ctx):
    """Start metrics collector daemon once."""
    if ctx.metrics_collector_started:
        return
    with ctx.metrics_collector_start_lock:
        if ctx.metrics_collector_started:
            return
        watcher = threading.Thread(target=metrics_collector_loop, args=(ctx,), daemon=True)
        watcher.start()
        ctx.metrics_collector_started = True


def get_cached_dashboard_metrics(ctx):
    """Return last metrics snapshot, or a safe default payload."""
    with ctx.metrics_cache_cond:
        if ctx.metrics_cache_payload:
            return dict(ctx.metrics_cache_payload)
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
        "players_online": "unknown",
        "tick_rate": "unknown",
        "session_duration": "--",
        "idle_countdown": "--:--",
        "backup_status": "Idle",
        "backup_status_class": "stat-yellow",
        "last_backup_time": "--",
        "next_backup_time": "--",
        "server_time": ctx.get_server_time_text(),
        "rcon_enabled": ctx.is_rcon_enabled(),
    }

