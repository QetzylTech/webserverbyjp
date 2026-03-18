"""Session lifecycle and background watcher services."""
import time
from app.services.worker_scheduler import WorkerSpec, start_worker


def format_countdown(seconds):
    """Format remaining seconds as ``MM:SS`` with floor at zero."""
    if seconds <= 0:
        return "00:00"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"


def get_idle_countdown(ctx, service_status=None, players_online=None):
    """Return idle auto-stop countdown when server is active with zero players."""
    if service_status is None:
        service_status = ctx.get_status()
    if players_online is None:
        players_online = ctx.get_players_online()
    if service_status != "active" or players_online != "0":
        return "--:--"
    with ctx.idle_lock:
        if ctx.idle_zero_players_since is None:
            return format_countdown(ctx.IDLE_ZERO_PLAYERS_SECONDS)
        elapsed = time.time() - ctx.idle_zero_players_since
    remaining = ctx.IDLE_ZERO_PLAYERS_SECONDS - elapsed
    return format_countdown(remaining)


def idle_player_watcher(ctx):
    """Background loop that triggers auto-stop after sustained zero players."""
    while True:
        should_auto_stop = False
        try:
            service_status = ctx.get_status()
            players_online = ctx.get_players_online()
            now = time.time()

            # Decide shutdown while holding the countdown lock, but run the stop flow
            # outside the lock because publishing metrics reads the same countdown state.
            with ctx.idle_lock:
                if service_status == "active" and players_online == "0":
                    if ctx.idle_zero_players_since is None:
                        ctx.idle_zero_players_since = now
                    elif now - ctx.idle_zero_players_since >= ctx.IDLE_ZERO_PLAYERS_SECONDS:
                        intent_getter = getattr(ctx, "get_service_status_intent", None)
                        intent = ""
                        if callable(intent_getter):
                            try:
                                intent = str(intent_getter() or "").strip().lower()
                            except Exception:
                                intent = ""
                        should_auto_stop = intent != "shutting"
                        # Keep the countdown pinned at zero until service state leaves active.
                        ctx.idle_zero_players_since = now - ctx.IDLE_ZERO_PLAYERS_SECONDS
                else:
                    ctx.idle_zero_players_since = None
            if should_auto_stop:
                ctx.stop_server_automatically()
        except Exception as exc:
            ctx.log_mcweb_exception("idle_player_watcher", exc)

        interval = ctx.IDLE_CHECK_INTERVAL_ACTIVE_SECONDS if ctx.get_status() == "active" else ctx.IDLE_CHECK_INTERVAL_OFF_SECONDS
        time.sleep(interval)


def start_idle_player_watcher(ctx):
    """Start the idle watcher daemon thread once per process."""
    start_worker(
        ctx,
        WorkerSpec(
            name="idle-player-watcher",
            target=idle_player_watcher,
            args=(ctx,),
            interval_source=getattr(ctx, "IDLE_CHECK_INTERVAL_ACTIVE_SECONDS", None),
            stop_signal_name="idle_player_watcher_stop_event",
            health_marker="idle_player_watcher",
        ),
    )


def backup_session_watcher(ctx):
    """Background loop that triggers periodic and session-end backups."""
    while True:
        try:
            backup_state = ctx.backup_state
            now = time.time()
            service_status = ctx.get_status()
            is_running = service_status == "active"
            is_off = service_status in ("inactive", "failed")

            should_run_periodic_backup = False
            should_run_shutdown_backup = False
            periodic_due_runs = 0

            session_started_at = ctx.read_session_start_time()

            # Evaluate and mutate backup scheduling state atomically.
            with backup_state.lock:
                if is_running:
                    if session_started_at is not None:
                        due_runs = int(max(0, now - session_started_at) // ctx.BACKUP_INTERVAL_SECONDS)
                        if due_runs > backup_state.periodic_runs:
                            should_run_periodic_backup = True
                            periodic_due_runs = due_runs
                elif is_off and session_started_at is not None:
                    should_run_shutdown_backup = True

            if should_run_periodic_backup:
                if ctx.run_backup_script(count_skip_as_success=False, trigger="auto"):
                    with backup_state.lock:
                        backup_state.periodic_runs = max(backup_state.periodic_runs, periodic_due_runs)

            if should_run_shutdown_backup:
                if ctx.run_backup_script(count_skip_as_success=False, trigger="session_end"):
                    ctx.clear_session_start_time()
                    with backup_state.lock:
                        backup_state.periodic_runs = 0
        except Exception as exc:
            ctx.log_mcweb_exception("backup_session_watcher", exc)

        interval = ctx.BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS if ctx.get_status() == "active" else ctx.BACKUP_WATCH_INTERVAL_OFF_SECONDS
        time.sleep(interval)


def start_backup_session_watcher(ctx):
    """Start the backup session watcher daemon thread."""
    start_worker(
        ctx,
        WorkerSpec(
            name="backup-session-watcher",
            target=backup_session_watcher,
            args=(ctx,),
            interval_source=getattr(ctx, "BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS", None),
            stop_signal_name="backup_session_watcher_stop_event",
            health_marker="backup_session_watcher",
        ),
    )


def _run_low_storage_emergency_shutdown(ctx):
    """Warn players via RCON, wait 30s, then force an emergency shutdown backup."""
    try:
        warning = (
            "say [ALERT] Server storage is critically low (<10% free). "
            "Emergency shutdown in 30 seconds."
        )
        if ctx.get_status() == "active" and ctx.is_rcon_enabled():
            try:
                ctx._run_mcrcon(warning, timeout=8)
            except Exception as exc:
                ctx.log_mcweb_exception("low_storage_rcon_warn", exc)

        time.sleep(30)
        if ctx.get_status() == "active":
            ctx.set_service_status_intent("shutting")
            ctx.graceful_stop_minecraft(trigger="emergency")
            ctx.clear_session_start_time()
            ctx.reset_backup_schedule_state()
            ctx.log_mcweb_action("emergency-shutdown", rejection_message="Low storage emergency shutdown executed.")
    except Exception as exc:
        ctx.log_mcweb_exception("low_storage_emergency_shutdown", exc)
    finally:
        with ctx.storage_emergency_lock:
            ctx.storage_emergency_active = False


def storage_safety_watcher(ctx):
    """Trigger emergency shutdown workflow when storage stays below safe threshold."""
    while True:
        try:
            service_status = ctx.get_status()
            guard = getattr(ctx, "storage_guard", None)
            if guard is not None:
                try:
                    low_storage = bool(guard.is_below_minimum(ctx))
                    emergency = bool(guard.needs_emergency_shutdown(ctx))
                except Exception:
                    low_storage = ctx.is_storage_low()
                    emergency = low_storage
            else:
                low_storage = ctx.is_storage_low()
                emergency = low_storage

            if service_status == "active" and emergency:
                should_start = False
                with ctx.storage_emergency_lock:
                    if not ctx.storage_emergency_active:
                        ctx.storage_emergency_active = True
                        should_start = True
                if should_start:
                    message = ctx.low_storage_error_message()
                    if guard is not None:
                        try:
                            message = guard.emergency_message(ctx)
                        except Exception:
                            message = ctx.low_storage_error_message()
                    ctx.log_mcweb_action("emergency-shutdown", rejection_message=message)
                    start_worker(
                        ctx,
                        WorkerSpec(
                            name="storage-emergency-shutdown",
                            target=_run_low_storage_emergency_shutdown,
                            args=(ctx,),
                            exception_policy="log_and_continue",
                            health_marker="storage_emergency_shutdown",
                        ),
                    )
            elif not low_storage:
                with ctx.storage_emergency_lock:
                    ctx.storage_emergency_active = False
        except Exception as exc:
            ctx.log_mcweb_exception("storage_safety_watcher", exc)

        interval = (
            ctx.STORAGE_SAFETY_CHECK_INTERVAL_ACTIVE_SECONDS
            if ctx.get_status() == "active"
            else ctx.STORAGE_SAFETY_CHECK_INTERVAL_OFF_SECONDS
        )
        time.sleep(interval)


def start_storage_safety_watcher(ctx):
    """Start the low-storage safety watcher daemon thread."""
    start_worker(
        ctx,
        WorkerSpec(
            name="storage-safety-watcher",
            target=storage_safety_watcher,
            args=(ctx,),
            interval_source=getattr(ctx, "STORAGE_SAFETY_CHECK_INTERVAL_ACTIVE_SECONDS", None),
            stop_signal_name="storage_safety_watcher_stop_event",
            health_marker="storage_safety_watcher",
        ),
    )


def initialize_session_tracking(ctx):
    """Initialize session file and periodic backup counters on process startup."""
    ctx.ensure_session_file()
    backup_state = ctx.backup_state
    service_status = ctx.get_status()
    session_start = ctx.read_session_start_time()

    if service_status in ctx.OFF_STATES:
        ctx.clear_session_start_time()
        return

    if session_start is None:
        ctx.write_session_start_time()
        with backup_state.lock:
            backup_state.periodic_runs = 0
        return

    with backup_state.lock:
        backup_state.periodic_runs = int(max(0, time.time() - session_start) // ctx.BACKUP_INTERVAL_SECONDS)


def status_state_note(ctx):
    """Return compact status note for session-state related error responses."""
    try:
        service_status = ctx.get_status()
        session_raw = ""
        if ctx.ensure_session_file():
            session_raw = ctx.session_state.session_file.read_text(encoding="utf-8").strip()
        return f"service={service_status}, session_file={'<empty>' if not session_raw else session_raw}"
    except Exception as exc:
        ctx.log_mcweb_exception("_status_state_note", exc)
        return "service=unknown, session_file=unreadable"

