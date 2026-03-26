"""Minecraft log-stream use cases."""

from collections import deque
import time
from pathlib import Path
from typing import Any

from app.ports import ports
from app.services.worker_scheduler import WorkerSpec, start_worker


LogSourceSettings = dict[str, object]


def _settings_path(value: object) -> Path:
    if isinstance(value, Path):
        return value
    return Path(str(value or ""))


def _settings_text_limit(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _file_source_settings(source: str, context: str, path: Path, text_limit: int) -> LogSourceSettings:
    return {
        "source": source,
        "type": "file",
        "context": context,
        "path": path,
        "text_limit": text_limit,
    }


def _minecraft_live_log_path(ctx: Any) -> Path:
    return Path(getattr(ctx, "MINECRAFT_LOGS_DIR", Path())) / "latest.log"


def is_rcon_noise_line(line: object) -> bool:
    lower = str(line or "").lower()
    if "thread rcon client" in lower:
        return True
    if "minecraft/rconclient" in lower and "shutting down" in lower:
        return True
    return False


def normalize_log_source(ctx: Any, source: object) -> str | None:
    normalized = str(source or "").strip().lower()
    if normalized not in ctx.LOG_SOURCE_KEYS:
        return None
    return str(normalized)


def log_source_settings(ctx: Any, source: object) -> LogSourceSettings | None:
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return None
    if normalized == "minecraft":
        stream_mode = str(ports.log.minecraft_log_stream_mode() or "journal").strip().lower()
        latest_log_path = _minecraft_live_log_path(ctx)
        if stream_mode == "journal" and latest_log_path.exists():
            return {
                "source": normalized,
                "type": "file_poll",
                "context": "minecraft_log_stream",
                "path": latest_log_path,
                "text_limit": ctx.MINECRAFT_LOG_TEXT_LIMIT,
            }
        if stream_mode == "file_poll":
            return {
                "source": normalized,
                "type": "file_poll",
                "context": "minecraft_log_stream",
                "path": latest_log_path,
                "text_limit": ctx.MINECRAFT_LOG_TEXT_LIMIT,
            }
        return {
            "source": normalized,
            "type": "journal",
            "context": "minecraft_log_stream",
            "unit": ctx.SERVICE,
            "text_limit": ctx.MINECRAFT_LOG_TEXT_LIMIT,
        }
    file_sources = {
        "backup": _file_source_settings(normalized, "backup_log_stream", ctx.BACKUP_LOG_FILE, ctx.BACKUP_LOG_TEXT_LIMIT),
        "restore": _file_source_settings(normalized, "restore_log_stream", ctx.RESTORE_LOG_FILE, ctx.MCWEB_LOG_TEXT_LIMIT),
        "mcweb_log": _file_source_settings(normalized, "mcweb_log_stream", ctx.MCWEB_LOG_FILE, ctx.MCWEB_LOG_TEXT_LIMIT),
        "mcweb": _file_source_settings(normalized, "mcweb_action_log_stream", ctx.MCWEB_ACTION_LOG_FILE, ctx.MCWEB_ACTION_LOG_TEXT_LIMIT),
    }
    return file_sources.get(normalized)


def get_log_source_text(ctx: Any, source: object) -> str | None:
    settings = log_source_settings(ctx, source)
    if settings is None:
        return None
    normalized = str(settings["source"])
    cached_getters = {
        "minecraft": ctx._get_cached_minecraft_log_text,
        "backup": ctx._get_cached_backup_log_text,
        "mcweb": ctx._get_cached_mcweb_log_text,
    }
    getter = cached_getters.get(normalized)
    if getter is not None:
        result = getter()
        return None if result is None else str(result)
    if normalized == "mcweb_log":
        lines = ctx._read_recent_file_lines(
            _settings_path(settings["path"]),
            _settings_text_limit(settings["text_limit"]),
        )
        return "\n".join(lines).strip() or "(no logs)"
    if normalized == "restore":
        lines = ctx._read_recent_file_lines(
            _settings_path(settings["path"]),
            _settings_text_limit(settings["text_limit"]),
        )
        return "\n".join(lines).strip() or "(no logs)"
    return None


def drain_buffered_log_lines(ctx: Any, source: object) -> list[str]:
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return []
    stream_state = ctx.log_stream_states.get(normalized)
    if stream_state is None:
        return []
    with stream_state["lifecycle_lock"]:
        buffered = stream_state.get("buffered_lines")
        if not isinstance(buffered, deque) or not buffered:
            return []
        lines = list(buffered)
        buffered.clear()
        return lines





def publish_log_stream_line(ctx: Any, source: object, line: object) -> None:
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    if normalized == "minecraft":
        intent = str(ctx.get_service_status_intent() or "").strip().lower()
        if intent == "starting":
            pattern = getattr(ctx, "RCON_STARTUP_READY_PATTERN", None)
            if pattern is not None and pattern.search(str(line or "")):
                with ctx.rcon_startup_lock:
                    ctx.rcon_startup_ready = True
    stream_state = ctx.log_stream_states.get(normalized)
    if stream_state is None:
        return
    process_role = str(getattr(ctx, "PROCESS_ROLE", "all") or "all").strip().lower()
    with stream_state["lifecycle_lock"]:
        has_clients = int(stream_state.get("clients", 0) or 0) > 0
        buffered = stream_state.get("buffered_lines")
        if not has_clients and isinstance(buffered, deque):
            buffered.append(str(line or ""))
    if has_clients:
        with stream_state["cond"]:
            db_event_id = 0
            try:
                db_event_id = int(
                    ports.store.append_event(
                        ctx.APP_STATE_DB_PATH,
                        topic=f"log:{normalized}",
                        payload={"line": str(line or "")},
                    )
                    or 0
                )
            except Exception:
                db_event_id = 0
            stream_state["seq"] = int(db_event_id or (stream_state["seq"] + 1))
            stream_state["events"].append((stream_state["seq"], line))
            stream_state["cond"].notify_all()
    appenders = {
        "minecraft": ctx._append_minecraft_log_cache_line,
        "backup": ctx._append_backup_log_cache_line,
        "mcweb": ctx._append_mcweb_log_cache_line,
    }
    appender = appenders.get(normalized)
    if appender is not None:
        appender(line)


def line_matches_crash_marker(ctx: Any, line: object) -> bool:
    clean = str(line or "").strip()
    if not clean:
        return False
    return any(marker in clean for marker in ctx.CRASH_STOP_MARKERS)


def crash_stop_after_grace(ctx: Any, trigger_line: object) -> None:
    try:
        time.sleep(ctx.CRASH_STOP_GRACE_SECONDS)
        if ctx.get_status() == "active":
            stopped = ctx.stop_service_runtime()
            if stopped:
                ctx.clear_session_start_time()
                ctx.reset_backup_schedule_state()
                ctx.log_mcweb_action("auto-stop-crash", command=f"marker={trigger_line} grace={ctx.CRASH_STOP_GRACE_SECONDS}s")
            else:
                ctx.log_mcweb_action(
                    "auto-stop-crash",
                    command=f"marker={trigger_line} grace={ctx.CRASH_STOP_GRACE_SECONDS}s",
                    rejection_message="service stop did not reach inactive/failed within timeout.",
                )
    finally:
        with ctx.crash_stop_lock:
            ctx.crash_stop_timer_active = False


def schedule_crash_stop_if_needed(ctx: Any, line: object) -> None:
    if not line_matches_crash_marker(ctx, line):
        return
    ctx.set_service_status_intent("crashed")
    with ctx.crash_stop_lock:
        if ctx.crash_stop_timer_active:
            return
        ctx.crash_stop_timer_active = True
    start_worker(
        ctx,
        WorkerSpec(
            name="crash-stop-after-grace",
            target=crash_stop_after_grace,
            args=(ctx, line),
            interval_source=getattr(ctx, "CRASH_STOP_GRACE_SECONDS", None),
            stop_signal_name="crash_stop_after_grace_event",
            health_marker="crash_stop_after_grace",
        ),
    )


def log_source_fetcher_loop(ctx: Any, source: object) -> None:
    settings = log_source_settings(ctx, source)
    if settings is None:
        return
    normalized = str(settings["source"])
    off_states = {str(item or "").strip().lower() for item in getattr(ctx, "OFF_STATES", {"inactive", "failed"})}
    backup_status_cache_at = 0.0
    backup_status_cache_value = False
    file_poll_offset = 0
    follow_from_end_initialized = False

    def _allow_background_minecraft_follow() -> bool:
        if normalized != "minecraft":
            return False
        intent = str(ctx.get_service_status_intent() or "").strip().lower()
        if intent == "starting":
            return True
        service_status = str(ctx.get_status() or "").strip().lower()
        return service_status not in off_states

    def _allow_background_backup_follow() -> bool:
        nonlocal backup_status_cache_at, backup_status_cache_value
        if normalized != "backup":
            return False
        now = time.time()
        if (now - backup_status_cache_at) < 1.0:
            return backup_status_cache_value
        active = False
        try:
            status_text, _ = ctx.get_backup_status()
            active = str(status_text or "").strip().lower() in {"running", "queued"}
        except Exception:
            active = False
        backup_status_cache_at = now
        backup_status_cache_value = active
        return active

    def _allow_background_follow() -> bool:
        if _allow_background_minecraft_follow():
            return True
        if _allow_background_backup_follow():
            return True
        return False

    def _refresh_idle_log_cache() -> None:
        if normalized == "minecraft":
            loader = getattr(ctx, "_load_minecraft_log_cache_from_journal", None)
            if callable(loader):
                loader()
        elif normalized == "backup":
            loader = getattr(ctx, "_load_backup_log_cache_from_disk", None)
            if callable(loader):
                loader()
        # Control panel logs are only updated when a client is connected.

    def _load_offset_state(stream_state: dict[str, Any]) -> tuple[int, bool]:
        with stream_state["lifecycle_lock"]:
            return int(stream_state.get("file_offset", 0) or 0), bool(stream_state.get("follow_initialized", False))

    def _store_offset_state(stream_state: dict[str, Any], offset: int, initialized: bool) -> None:
        with stream_state["lifecycle_lock"]:
            stream_state["file_offset"] = int(offset)
            stream_state["follow_initialized"] = bool(initialized)

    def _wait_for_clients_or_timeout(stream_state: dict[str, Any], timeout_seconds: float) -> None:
        timeout_value = max(0.1, float(timeout_seconds or 0.1))
        with stream_state["cond"]:
            stream_state["cond"].wait_for(
                lambda: int(stream_state.get("clients", 0) or 0) > 0,
                timeout=timeout_value,
            )

    def _read_file_updates(stream_state: dict[str, Any], path: Path, *, allow_break_on_no_clients: bool) -> None:
        nonlocal file_poll_offset, follow_from_end_initialized
        if not path.exists():
            file_poll_offset = 0
            follow_from_end_initialized = False
            _store_offset_state(stream_state, file_poll_offset, follow_from_end_initialized)
            time.sleep(1)
            return
        try:
            file_size = int(path.stat().st_size)
        except OSError:
            file_size = 0
        if not follow_from_end_initialized and file_poll_offset == 0:
            # Initial "live follow" should not replay the entire existing file.
            file_poll_offset = file_size
            follow_from_end_initialized = True
            _store_offset_state(stream_state, file_poll_offset, follow_from_end_initialized)
            return
        if file_poll_offset > file_size:
            file_poll_offset = 0
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            fh.seek(file_poll_offset)
            for line in fh:
                if allow_break_on_no_clients:
                    with stream_state["lifecycle_lock"]:
                        if stream_state["clients"] <= 0 and not _allow_background_follow():
                            break
                clean = line.rstrip("\n")
                if not clean:
                    continue
                if normalized == "minecraft" and is_rcon_noise_line(clean):
                    continue
                publish_log_stream_line(ctx, normalized, clean)
                if normalized == "minecraft":
                    schedule_crash_stop_if_needed(ctx, clean)
            file_poll_offset = int(fh.tell())
        _store_offset_state(stream_state, file_poll_offset, follow_from_end_initialized)

    while True:
        stream_state = ctx.log_stream_states.get(normalized)
        if stream_state is None:
            return
        file_poll_offset, follow_from_end_initialized = _load_offset_state(stream_state)
        with stream_state["lifecycle_lock"]:
            client_count = stream_state["clients"]
        if client_count <= 0:
            idle_poll_seconds = float(getattr(ctx, "LOG_FETCHER_IDLE_POLL_SECONDS", 15.0) or 15.0)
            if settings["type"] in {"file", "file_poll"}:
                if not _allow_background_follow():
                    try:
                        service_status = str(ctx.get_status() or "").strip().lower()
                    except Exception:
                        service_status = ""
                    if service_status in off_states:
                        _wait_for_clients_or_timeout(stream_state, idle_poll_seconds)
                        continue
                _read_file_updates(
                    stream_state,
                    _settings_path(settings["path"]),
                    allow_break_on_no_clients=False,
                )
                _wait_for_clients_or_timeout(stream_state, idle_poll_seconds)
                continue
            if _allow_background_follow():
                _refresh_idle_log_cache()
                _wait_for_clients_or_timeout(stream_state, idle_poll_seconds)
                continue
            _wait_for_clients_or_timeout(stream_state, idle_poll_seconds)
            continue

        proc = None
        try:
            if settings["type"] == "file_poll":
                _read_file_updates(
                    stream_state,
                    _settings_path(settings["path"]),
                    allow_break_on_no_clients=True,
                )
                time.sleep(1)
                continue
            if settings["type"] == "journal":
                proc = ports.log.minecraft_open_follow_logs_process(str(settings["unit"]), ctx.MINECRAFT_LOGS_DIR)
                if not proc:
                    time.sleep(1)
                    continue
            else:
                _read_file_updates(
                    stream_state,
                    _settings_path(settings["path"]),
                    allow_break_on_no_clients=True,
                )
                time.sleep(1)
                continue

            with stream_state["lifecycle_lock"]:
                stream_state["proc"] = proc
            for line in ports.log.iter_process_lines(proc):
                with stream_state["lifecycle_lock"]:
                    if stream_state["clients"] <= 0 and not _allow_background_follow():
                        break
                clean = line.rstrip("\n")
                if not clean:
                    continue
                if normalized == "minecraft" and is_rcon_noise_line(clean):
                    continue
                publish_log_stream_line(ctx, normalized, clean)
                if normalized == "minecraft":
                    schedule_crash_stop_if_needed(ctx, clean)
        except Exception as exc:
            ctx.log_mcweb_exception(settings["context"], exc)
        finally:
            with stream_state["lifecycle_lock"]:
                stream_state["proc"] = None
            ports.log.terminate_process(proc)
        time.sleep(1)


def ensure_log_stream_fetcher_started(ctx: Any, source: object) -> None:
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    state = ctx.log_stream_states.get(normalized)
    if state is None or state["started"]:
        return
    with state["lifecycle_lock"]:
        if state["started"]:
            return
        start_worker(
            ctx,
            WorkerSpec(
                name=f"log-stream-fetcher-{normalized}",
                target=log_source_fetcher_loop,
                args=(ctx, normalized),
                interval_source=getattr(ctx, "LOG_FETCHER_IDLE_SLEEP_SECONDS", None),
                stop_signal_name=f"log_stream_fetcher_stop_event_{normalized}",
                health_marker=f"log_stream_fetcher_{normalized}",
            ),
        )
        state["started"] = True


def increment_log_stream_clients(ctx: Any, source: object) -> None:
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    stream_state = ctx.log_stream_states.get(normalized)
    if stream_state is None:
        return
    with stream_state["lifecycle_lock"]:
        stream_state["clients"] += 1
    with stream_state["cond"]:
        stream_state["cond"].notify_all()


def decrement_log_stream_clients(ctx: Any, source: object) -> None:
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    stream_state = ctx.log_stream_states.get(normalized)
    if stream_state is None:
        return
    with stream_state["lifecycle_lock"]:
        stream_state["clients"] = max(0, stream_state["clients"] - 1)
        proc = stream_state["proc"]
    with stream_state["cond"]:
        stream_state["cond"].notify_all()
    ports.log.terminate_process(proc)

