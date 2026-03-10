"""Dashboard log cache runtime helpers."""
from app.ports import ports


def _is_rcon_noise_line(line):
    """Return whether a minecraft log line is known RCON shutdown/startup noise."""
    lower = (line or "").lower()
    if "thread rcon client" in lower:
        return True
    if "minecraft/rconclient" in lower and "shutting down" in lower:
        return True
    return False


def _load_minecraft_log_cache_from_latest_file(ctx, max_visible_lines=500):
    """Load recent minecraft file logs, preferring non-RCON-noise lines."""
    lines = []
    latest_path = None
    try:
        candidates = [p for p in ctx.MINECRAFT_LOGS_DIR.glob("*.log") if p.is_file()]
        if candidates:
            latest_path = max(candidates, key=lambda p: p.stat().st_mtime_ns)
    except OSError:
        latest_path = None

    if latest_path is not None:
        # Read a larger tail window so filtering still leaves enough visible lines.
        source_lines = ctx._read_recent_file_lines(latest_path, max(max_visible_lines * 8, 2000))
        filtered = [line for line in source_lines if not _is_rcon_noise_line(line)]
        lines = filtered[-max_visible_lines:]
    with ctx.minecraft_log_cache_lock:
        ctx.minecraft_log_cache_lines.clear()
        ctx.minecraft_log_cache_lines.extend(lines)
        ctx.minecraft_log_cache_loaded = True


def load_backup_log_cache_from_disk(ctx):
    """Reload backup log cache from disk into bounded in-memory storage."""
    lines = ctx._read_recent_file_lines(ctx.BACKUP_LOG_FILE, ctx.BACKUP_LOG_TEXT_LIMIT)
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
    """Prime minecraft log cache from platform-selected runtime log source."""
    output = ""
    try:
        output = str(
            ports.log.minecraft_load_recent_logs(
                ctx.SERVICE,
                ctx.MINECRAFT_LOGS_DIR,
                tail_lines=ctx.MINECRAFT_JOURNAL_TAIL_LINES,
                timeout=ctx.JOURNAL_LOAD_TIMEOUT_SECONDS,
            )
            or ""
        ).strip()
    except Exception as exc:
        if not ports.log.is_timeout_error(exc):
            ctx.log_mcweb_exception("load_minecraft_log_cache_from_journal", exc)
            output = ""
        else:
            ctx.log_mcweb_log(
                "log-load-timeout",
                command=f"minecraft_load_recent_logs service={ctx.SERVICE}",
                rejection_message=f"Timed out after {ctx.JOURNAL_LOAD_TIMEOUT_SECONDS:.1f}s.",
            )
            output = ""
    if not output:
        _load_minecraft_log_cache_from_latest_file(ctx, max_visible_lines=ctx.MINECRAFT_LOG_VISIBLE_LINES)
        return
    lines = output.splitlines()
    lines = [line for line in lines if not _is_rcon_noise_line(line)]
    if len(lines) > ctx.MINECRAFT_LOG_TEXT_LIMIT:
        lines = lines[-ctx.MINECRAFT_LOG_TEXT_LIMIT:]
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
    lines = ctx._read_recent_file_lines(ctx.MCWEB_ACTION_LOG_FILE, ctx.MCWEB_ACTION_LOG_TEXT_LIMIT)
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
