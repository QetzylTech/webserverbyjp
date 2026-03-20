"""Dashboard log cache runtime helpers."""
from pathlib import Path
from threading import Lock
from typing import Any

from app.ports import ports


def _load_file_log_cache_from_disk(
    ctx: Any,
    *,
    path: Path,
    limit: int,
    lock: Lock,
    lines_attr: str,
    loaded_attr: str,
    mtime_attr: str,
) -> None:
    """Load a file-backed log cache into memory with its mtime marker."""
    lines = ctx._read_recent_file_lines(path, limit)
    mtime_ns = ctx._safe_file_mtime_ns(path)
    with lock:
        getattr(ctx, lines_attr).clear()
        getattr(ctx, lines_attr).extend(lines)
        setattr(ctx, loaded_attr, True)
        setattr(ctx, mtime_attr, mtime_ns)


def _append_file_log_cache_line(
    ctx: Any,
    *,
    line: object,
    path: Path,
    lock: Lock,
    lines_attr: str,
    loaded_attr: str,
    mtime_attr: str,
) -> None:
    """Append one log line to a file-backed cache and refresh its mtime marker."""
    clean = str(line or "").rstrip("\r\n")
    if not clean:
        return
    with lock:
        getattr(ctx, lines_attr).append(clean)
        setattr(ctx, loaded_attr, True)
        setattr(ctx, mtime_attr, ctx._safe_file_mtime_ns(path))


def _get_cached_file_log_text(
    ctx: Any,
    *,
    path: Path,
    limit: int,
    lock: Lock,
    lines_attr: str,
    loaded_attr: str,
    mtime_attr: str,
) -> str:
    """Return cached log text, reloading only when on-disk mtime changes."""
    current_mtime_ns = ctx._safe_file_mtime_ns(path)
    with lock:
        loaded = bool(getattr(ctx, loaded_attr))
        cached_mtime_ns = getattr(ctx, mtime_attr)
        if loaded and cached_mtime_ns == current_mtime_ns:
            return "\n".join(getattr(ctx, lines_attr)).strip() or "(no logs)"
    _load_file_log_cache_from_disk(
        ctx,
        path=path,
        limit=limit,
        lock=lock,
        lines_attr=lines_attr,
        loaded_attr=loaded_attr,
        mtime_attr=mtime_attr,
    )
    with lock:
        return "\n".join(getattr(ctx, lines_attr)).strip() or "(no logs)"


def _is_rcon_noise_line(line: object) -> bool:
    """Return whether a minecraft log line is known RCON shutdown/startup noise."""
    lower = str(line or "").lower()
    if "thread rcon client" in lower:
        return True
    if "minecraft/rconclient" in lower and "shutting down" in lower:
        return True
    return False


def _minecraft_log_lines_from_latest_file(ctx: Any, max_visible_lines: int = 500) -> list[str]:
    """Return recent minecraft file log lines, preferring non-RCON-noise lines."""
    lines: list[str] = []
    latest_path = None
    try:
        candidates = [p for p in ctx.MINECRAFT_LOGS_DIR.glob("*.log") if p.is_file()]
        if candidates:
            latest_path = max(candidates, key=lambda p: p.stat().st_mtime_ns)
    except OSError:
        latest_path = None

    if latest_path is not None:
        # Read a larger tail window so filtering still leaves enough visible lines.
        source_lines = [
            str(line)
            for line in ctx._read_recent_file_lines(latest_path, max(max_visible_lines * 8, 2000))
        ]
        filtered = [line for line in source_lines if not _is_rcon_noise_line(line)]
        if len(filtered) >= max_visible_lines:
            return filtered[-max_visible_lines:]
        return source_lines[-max_visible_lines:]
    return lines


def _load_minecraft_log_cache_from_latest_file(ctx: Any, max_visible_lines: int = 500) -> list[str]:
    """Load recent minecraft file logs into cache."""
    lines = _minecraft_log_lines_from_latest_file(ctx, max_visible_lines=max_visible_lines)
    with ctx.minecraft_log_cache_lock:
        ctx.minecraft_log_cache_lines.clear()
        ctx.minecraft_log_cache_lines.extend(lines)
        ctx.minecraft_log_cache_loaded = True
    return lines


def load_backup_log_cache_from_disk(ctx: Any) -> None:
    """Reload backup log cache from disk into bounded in-memory storage."""
    _load_file_log_cache_from_disk(
        ctx,
        path=ctx.BACKUP_LOG_FILE,
        limit=ctx.BACKUP_LOG_TEXT_LIMIT,
        lock=ctx.backup_log_cache_lock,
        lines_attr="backup_log_cache_lines",
        loaded_attr="backup_log_cache_loaded",
        mtime_attr="backup_log_cache_mtime_ns",
    )


def append_backup_log_cache_line(ctx: Any, line: object) -> None:
    """Append one backup log line into cache, updating file mtime hint."""
    _append_file_log_cache_line(
        ctx,
        line=line,
        path=ctx.BACKUP_LOG_FILE,
        lock=ctx.backup_log_cache_lock,
        lines_attr="backup_log_cache_lines",
        loaded_attr="backup_log_cache_loaded",
        mtime_attr="backup_log_cache_mtime_ns",
    )


def get_cached_backup_log_text(ctx: Any) -> str:
    """Return backup log text, reloading only when on-disk mtime changes."""
    return _get_cached_file_log_text(
        ctx,
        path=ctx.BACKUP_LOG_FILE,
        limit=ctx.BACKUP_LOG_TEXT_LIMIT,
        lock=ctx.backup_log_cache_lock,
        lines_attr="backup_log_cache_lines",
        loaded_attr="backup_log_cache_loaded",
        mtime_attr="backup_log_cache_mtime_ns",
    )


def load_minecraft_log_cache_from_journal(ctx: Any) -> None:
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
    if len(lines) < ctx.MINECRAFT_LOG_VISIBLE_LINES:
        # Journal output may be sparse; fall back to latest.log for an initial full tail.
        file_lines = _minecraft_log_lines_from_latest_file(ctx, max_visible_lines=ctx.MINECRAFT_LOG_VISIBLE_LINES)
        if len(file_lines) >= len(lines):
            with ctx.minecraft_log_cache_lock:
                ctx.minecraft_log_cache_lines.clear()
                ctx.minecraft_log_cache_lines.extend(file_lines)
                ctx.minecraft_log_cache_loaded = True
            return
    if len(lines) > ctx.MINECRAFT_LOG_TEXT_LIMIT:
        lines = lines[-ctx.MINECRAFT_LOG_TEXT_LIMIT:]
    with ctx.minecraft_log_cache_lock:
        ctx.minecraft_log_cache_lines.clear()
        ctx.minecraft_log_cache_lines.extend(lines)
        ctx.minecraft_log_cache_loaded = True


def append_minecraft_log_cache_line(ctx: Any, line: object) -> None:
    """Append one minecraft journal line into cache."""
    clean = str(line or "").rstrip("\r\n")
    if not clean:
        return
    # Keep the cache bounded by the deque maxlen; just append the newest line.
    with ctx.minecraft_log_cache_lock:
        ctx.minecraft_log_cache_lines.append(clean)
        ctx.minecraft_log_cache_loaded = True


def get_cached_minecraft_log_text(ctx: Any) -> str:
    """Return minecraft log cache, loading initial snapshot on demand."""
    with ctx.minecraft_log_cache_lock:
        if ctx.minecraft_log_cache_loaded and len(ctx.minecraft_log_cache_lines) >= ctx.MINECRAFT_LOG_VISIBLE_LINES:
            return "\n".join(ctx.minecraft_log_cache_lines).strip() or "(no logs)"
    load_minecraft_log_cache_from_journal(ctx)
    with ctx.minecraft_log_cache_lock:
        return "\n".join(ctx.minecraft_log_cache_lines).strip() or "(no logs)"


def load_mcweb_log_cache_from_disk(ctx: Any) -> None:
    """Reload mcweb action log cache from disk."""
    _load_file_log_cache_from_disk(
        ctx,
        path=ctx.MCWEB_ACTION_LOG_FILE,
        limit=ctx.MCWEB_ACTION_LOG_TEXT_LIMIT,
        lock=ctx.mcweb_log_cache_lock,
        lines_attr="mcweb_log_cache_lines",
        loaded_attr="mcweb_log_cache_loaded",
        mtime_attr="mcweb_log_cache_mtime_ns",
    )


def append_mcweb_log_cache_line(ctx: Any, line: object) -> None:
    """Append one mcweb action log line into cache."""
    _append_file_log_cache_line(
        ctx,
        line=line,
        path=ctx.MCWEB_ACTION_LOG_FILE,
        lock=ctx.mcweb_log_cache_lock,
        lines_attr="mcweb_log_cache_lines",
        loaded_attr="mcweb_log_cache_loaded",
        mtime_attr="mcweb_log_cache_mtime_ns",
    )


def get_cached_mcweb_log_text(ctx: Any) -> str:
    """Return mcweb action log text, refreshing if file changed."""
    return _get_cached_file_log_text(
        ctx,
        path=ctx.MCWEB_ACTION_LOG_FILE,
        limit=ctx.MCWEB_ACTION_LOG_TEXT_LIMIT,
        lock=ctx.mcweb_log_cache_lock,
        lines_attr="mcweb_log_cache_lines",
        loaded_attr="mcweb_log_cache_loaded",
        mtime_attr="mcweb_log_cache_mtime_ns",
    )

