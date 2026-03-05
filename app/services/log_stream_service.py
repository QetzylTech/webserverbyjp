"""Minecraft log-stream use cases."""

import time

from app.ports import ports
from app.services.worker_scheduler import WorkerSpec, start_worker


def _file_source_settings(source, context, path, text_limit):
    return {
        "source": source,
        "type": "file",
        "context": context,
        "path": path,
        "text_limit": text_limit,
    }


def is_rcon_noise_line(line):
    lower = (line or "").lower()
    if "thread rcon client" in lower:
        return True
    if "minecraft/rconclient" in lower and "shutting down" in lower:
        return True
    return False


def normalize_log_source(ctx, source):
    normalized = (source or "").strip().lower()
    if normalized not in ctx.LOG_SOURCE_KEYS:
        return None
    return normalized


def log_source_settings(ctx, source):
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return None
    if normalized == "minecraft":
        stream_mode = str(ports.log.minecraft_log_stream_mode() or "journal").strip().lower()
        if stream_mode == "file_poll":
            return {
                "source": normalized,
                "type": "file_poll",
                "context": "minecraft_log_stream",
                "path": ctx.MINECRAFT_LOGS_DIR / "latest.log",
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
        "mcweb_log": _file_source_settings(normalized, "mcweb_log_stream", ctx.MCWEB_LOG_FILE, ctx.MCWEB_LOG_TEXT_LIMIT),
        "mcweb": _file_source_settings(normalized, "mcweb_action_log_stream", ctx.MCWEB_ACTION_LOG_FILE, ctx.MCWEB_ACTION_LOG_TEXT_LIMIT),
    }
    return file_sources.get(normalized)


def get_log_source_text(ctx, source):
    settings = log_source_settings(ctx, source)
    if settings is None:
        return None
    normalized = settings["source"]
    cached_getters = {
        "minecraft": ctx._get_cached_minecraft_log_text,
        "backup": ctx._get_cached_backup_log_text,
        "mcweb": ctx._get_cached_mcweb_log_text,
    }
    getter = cached_getters.get(normalized)
    if getter is not None:
        return getter()
    if normalized == "mcweb_log":
        lines = ctx._read_recent_file_lines(settings["path"], settings["text_limit"])
        return "\n".join(lines).strip() or "(no logs)"
    return None


def publish_log_stream_line(ctx, source, line):
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    state = ctx.log_stream_states.get(normalized)
    if state is None:
        return
    with state["cond"]:
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
        state["seq"] = int(db_event_id or (state["seq"] + 1))
        state["events"].append((state["seq"], line))
        state["cond"].notify_all()
    appenders = {
        "minecraft": ctx._append_minecraft_log_cache_line,
        "backup": ctx._append_backup_log_cache_line,
        "mcweb": ctx._append_mcweb_log_cache_line,
    }
    appender = appenders.get(normalized)
    if appender is not None:
        appender(line)


def line_matches_crash_marker(ctx, line):
    clean = (line or "").strip()
    if not clean:
        return False
    return any(marker in clean for marker in ctx.CRASH_STOP_MARKERS)


def crash_stop_after_grace(ctx, trigger_line):
    try:
        time.sleep(ctx.CRASH_STOP_GRACE_SECONDS)
        if ctx.get_status() == "active":
            stopped = ctx.stop_service_systemd()
            if stopped:
                ctx.clear_session_start_time()
                ctx.reset_backup_schedule_state()
                ctx.log_mcweb_action("auto-stop-crash", command=f"marker={trigger_line} grace={ctx.CRASH_STOP_GRACE_SECONDS}s")
            else:
                ctx.log_mcweb_action(
                    "auto-stop-crash",
                    command=f"marker={trigger_line} grace={ctx.CRASH_STOP_GRACE_SECONDS}s",
                    rejection_message="systemd stop did not reach inactive/failed within timeout.",
                )
    finally:
        with ctx.crash_stop_lock:
            ctx.crash_stop_timer_active = False


def schedule_crash_stop_if_needed(ctx, line):
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


def log_source_fetcher_loop(ctx, source):
    settings = log_source_settings(ctx, source)
    if settings is None:
        return
    normalized = settings["source"]
    file_poll_offset = 0

    while True:
        state = ctx.log_stream_states.get(normalized)
        if state is None:
            return
        with state["lifecycle_lock"]:
            client_count = state["clients"]
        if client_count <= 0:
            time.sleep(ctx.LOG_FETCHER_IDLE_SLEEP_SECONDS)
            continue

        proc = None
        try:
            if settings["type"] == "file_poll":
                path = settings["path"]
                if not path.exists():
                    file_poll_offset = 0
                    time.sleep(1)
                    continue
                try:
                    file_size = int(path.stat().st_size)
                except OSError:
                    file_size = 0
                if file_poll_offset > file_size:
                    file_poll_offset = 0
                with path.open("r", encoding="utf-8", errors="ignore") as fh:
                    fh.seek(file_poll_offset)
                    for line in fh:
                        with state["lifecycle_lock"]:
                            if state["clients"] <= 0:
                                break
                        clean = line.rstrip("\r\n")
                        if not clean:
                            continue
                        if normalized == "minecraft" and is_rcon_noise_line(clean):
                            continue
                        publish_log_stream_line(ctx, normalized, clean)
                        if normalized == "minecraft":
                            schedule_crash_stop_if_needed(ctx, clean)
                    file_poll_offset = int(fh.tell())
                time.sleep(1)
                continue
            if settings["type"] == "journal":
                proc = ports.log.minecraft_open_follow_logs_process(settings["unit"], ctx.MINECRAFT_LOGS_DIR)
                if not proc:
                    time.sleep(1)
                    continue
            else:
                path = settings["path"]
                if not path.exists():
                    time.sleep(1)
                    continue
                if file_poll_offset > int(path.stat().st_size if path.exists() else 0):
                    file_poll_offset = 0
                with path.open("r", encoding="utf-8", errors="ignore") as fh:
                    fh.seek(file_poll_offset)
                    for line in fh:
                        with state["lifecycle_lock"]:
                            if state["clients"] <= 0:
                                break
                        clean = line.rstrip("\r\n")
                        if not clean:
                            continue
                        publish_log_stream_line(ctx, normalized, clean)
                    file_poll_offset = int(fh.tell())
                time.sleep(1)
                continue

            with state["lifecycle_lock"]:
                state["proc"] = proc
            for line in ports.log.iter_process_lines(proc):
                with state["lifecycle_lock"]:
                    if state["clients"] <= 0:
                        break
                clean = line.rstrip("\r\n")
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
            with state["lifecycle_lock"]:
                state["proc"] = None
            ports.log.terminate_process(proc)
        time.sleep(1)


def ensure_log_stream_fetcher_started(ctx, source):
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


def increment_log_stream_clients(ctx, source):
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    state = ctx.log_stream_states.get(normalized)
    if state is None:
        return
    with state["lifecycle_lock"]:
        state["clients"] += 1


def decrement_log_stream_clients(ctx, source):
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    state = ctx.log_stream_states.get(normalized)
    if state is None:
        return
    with state["lifecycle_lock"]:
        state["clients"] = max(0, state["clients"] - 1)
        proc = state["proc"]
    ports.log.terminate_process(proc)
