"""Minecraft runtime probes, RCON, and log stream services."""

import subprocess
import threading
import time
import shutil
import re


def normalize_log_source(ctx, source):
    """Normalize and validate one log source key."""
    normalized = (source or "").strip().lower()
    if normalized not in ctx.LOG_SOURCE_KEYS:
        return None
    return normalized


def log_source_settings(ctx, source):
    """Return normalized log source settings for SSE/file/journal streams."""
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return None
    if normalized == "minecraft":
        return {
            "source": normalized,
            "type": "journal",
            "context": "minecraft_log_stream",
            "unit": ctx.SERVICE,
            "text_limit": 1000,
        }
    if normalized == "backup":
        return {
            "source": normalized,
            "type": "file",
            "context": "backup_log_stream",
            "path": ctx.BACKUP_LOG_FILE,
            "text_limit": 200,
        }
    if normalized == "mcweb_log":
        return {
            "source": normalized,
            "type": "file",
            "context": "mcweb_log_stream",
            "path": ctx.MCWEB_LOG_FILE,
            "text_limit": 200,
        }
    return {
        "source": normalized,
        "type": "file",
        "context": "mcweb_action_log_stream",
        "path": ctx.MCWEB_ACTION_LOG_FILE,
        "text_limit": 200,
    }


def get_log_source_text(ctx, source):
    """Return cached text payload for the requested log source."""
    settings = log_source_settings(ctx, source)
    if settings is None:
        return None
    normalized = settings["source"]
    if normalized == "minecraft":
        return ctx._get_cached_minecraft_log_text()
    if normalized == "backup":
        return ctx._get_cached_backup_log_text()
    if normalized == "mcweb_log":
        path = settings["path"]
        lines = ctx._read_recent_file_lines(path, settings["text_limit"])
        return "\n".join(lines).strip() or "(no logs)"
    return ctx._get_cached_mcweb_log_text()


def publish_log_stream_line(ctx, source, line):
    """Publish one log line to stream subscribers and cache backends."""
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    state = ctx.log_stream_states.get(normalized)
    if state is None:
        return
    with state["cond"]:
        state["seq"] += 1
        state["events"].append((state["seq"], line))
        state["cond"].notify_all()
    if normalized == "minecraft":
        ctx._append_minecraft_log_cache_line(line)
    elif normalized == "backup":
        ctx._append_backup_log_cache_line(line)
    elif normalized == "mcweb":
        ctx._append_mcweb_log_cache_line(line)


def line_matches_crash_marker(ctx, line):
    """Return whether a log line contains any configured crash marker."""
    clean = (line or "").strip()
    if not clean:
        return False
    return any(marker in clean for marker in ctx.CRASH_STOP_MARKERS)


def crash_stop_after_grace(ctx, trigger_line):
    """Stop the service after crash grace period if still active."""
    try:
        time.sleep(ctx.CRASH_STOP_GRACE_SECONDS)
        if ctx.get_status() == "active":
            stopped = ctx.stop_service_systemd()
            if stopped:
                ctx.clear_session_start_time()
                ctx.reset_backup_schedule_state()
                ctx.log_mcweb_action(
                    "auto-stop-crash",
                    command=f"marker={trigger_line} grace={ctx.CRASH_STOP_GRACE_SECONDS}s",
                )
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
    """Schedule one crash-stop timer when a crash marker appears."""
    if not line_matches_crash_marker(ctx, line):
        return
    ctx.set_service_status_intent("crashed")
    with ctx.crash_stop_lock:
        if ctx.crash_stop_timer_active:
            return
        ctx.crash_stop_timer_active = True
    worker = threading.Thread(target=crash_stop_after_grace, args=(ctx, line), daemon=True)
    worker.start()


def log_source_fetcher_loop(ctx, source):
    """Fetch source log lines continuously while subscribers are present."""
    settings = log_source_settings(ctx, source)
    if settings is None:
        return
    normalized = settings["source"]

    while True:
        state = ctx.log_stream_states.get(normalized)
        if state is None:
            return
        # Avoid keeping journalctl/tail processes alive when nobody is subscribed.
        with state["lifecycle_lock"]:
            client_count = state["clients"]
        if client_count <= 0:
            time.sleep(ctx.LOG_FETCHER_IDLE_SLEEP_SECONDS)
            continue

        proc = None
        try:
            if settings["type"] == "journal":
                cmd = ["journalctl", "-u", settings["unit"], "-f", "-n", "0", "--no-pager"]
            else:
                path = settings["path"]
                if not path.exists():
                    time.sleep(1)
                    continue
                cmd = ["tail", "-n", "0", "-F", str(path)]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            with state["lifecycle_lock"]:
                state["proc"] = proc

            if not proc.stdout:
                time.sleep(1)
                continue

            for line in proc.stdout:
                with state["lifecycle_lock"]:
                    if state["clients"] <= 0:
                        break
                clean = line.rstrip("\r\n")
                if not clean:
                    continue
                publish_log_stream_line(ctx, normalized, clean)
                if normalized == "minecraft":
                    schedule_crash_stop_if_needed(ctx, clean)
        except Exception as exc:
            ctx.log_mcweb_exception(settings["context"], exc)
        finally:
            with state["lifecycle_lock"]:
                state["proc"] = None
            if proc and proc.poll() is None:
                proc.terminate()

        time.sleep(1)


def ensure_log_stream_fetcher_started(ctx, source):
    """Start one background fetcher thread for a given log source."""
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    state = ctx.log_stream_states.get(normalized)
    if state is None:
        return
    if state["started"]:
        return
    with state["lifecycle_lock"]:
        if state["started"]:
            return
        watcher = threading.Thread(target=log_source_fetcher_loop, args=(ctx, normalized), daemon=True)
        watcher.start()
        state["started"] = True


def increment_log_stream_clients(ctx, source):
    """Increment active SSE client count for a log source."""
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    state = ctx.log_stream_states.get(normalized)
    if state is None:
        return
    with state["lifecycle_lock"]:
        state["clients"] += 1


def decrement_log_stream_clients(ctx, source):
    """Decrement SSE client count and terminate idle fetch process."""
    normalized = normalize_log_source(ctx, source)
    if normalized is None:
        return
    state = ctx.log_stream_states.get(normalized)
    if state is None:
        return
    with state["lifecycle_lock"]:
        state["clients"] = max(0, state["clients"] - 1)
        proc = state["proc"]
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def is_rcon_startup_ready(ctx, service_status=None):
    """Return whether startup logs indicate RCON commands should work."""
    if service_status is None:
        service_status = ctx.get_status()
    if service_status != "active":
        with ctx.rcon_startup_lock:
            ctx.rcon_startup_ready = False
        return False
    with ctx.rcon_startup_lock:
        if ctx.rcon_startup_ready:
            return True

    try:
        result = subprocess.run(
            ["journalctl", "-u", ctx.SERVICE, "-n", "500", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=ctx.RCON_STARTUP_JOURNAL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        ctx.log_mcweb_log(
            "rcon-startup-check-timeout",
            command=f"journalctl -u {ctx.SERVICE} -n 500",
            rejection_message=f"Timed out after {ctx.RCON_STARTUP_JOURNAL_TIMEOUT_SECONDS:.1f}s.",
        )
        return False
    except Exception as exc:
        ctx.log_mcweb_exception("is_rcon_startup_ready", exc)
        return False
    output = (result.stdout or "") + (result.stderr or "")
    ready = bool(ctx.RCON_STARTUP_READY_PATTERN.search(output))
    if ready:
        with ctx.rcon_startup_lock:
            ctx.rcon_startup_ready = True
    return ready


def candidate_mcrcon_bins():
    """Return preferred list of mcrcon binary candidates."""
    candidates = []
    found = shutil.which("mcrcon")
    if found:
        candidates.append(found)
    for path in ("/usr/bin/mcrcon", "/usr/local/bin/mcrcon", "/opt/mcrcon/mcrcon"):
        if path not in candidates:
            candidates.append(path)
    return candidates


def clean_rcon_output(text):
    """Strip ANSI and section-format control codes from RCON output."""
    cleaned = text or ""
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", cleaned)
    cleaned = re.sub(r"\u00a7.", "", cleaned)
    return cleaned


def refresh_rcon_config(ctx):
    """Reload and cache RCON password/port settings from server.properties."""
    now = time.time()
    with ctx.rcon_config_lock:
        if now - ctx.rcon_last_config_read_at < 60:
            return ctx.rcon_cached_password, ctx.rcon_cached_port, ctx.rcon_cached_enabled

        ctx.rcon_last_config_read_at = now
        parsed_password = None
        parsed_port = None

        for path in ctx.SERVER_PROPERTIES_CANDIDATES:
            if not path.exists():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue

            kv = {}
            for raw in lines:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                kv[key.strip()] = value.strip()

            if kv.get("enable-rcon", "").lower() == "false":
                continue

            candidate_password = kv.get("rcon.password", "").strip()
            if not candidate_password:
                continue

            parsed_password = candidate_password
            if kv.get("rcon.port", "").isdigit():
                parsed_port = int(kv.get("rcon.port"))
            break

        if parsed_password:
            ctx.rcon_cached_password = parsed_password
            ctx.rcon_cached_enabled = True
            if parsed_port:
                ctx.rcon_cached_port = parsed_port
        else:
            ctx.rcon_cached_password = None
            ctx.rcon_cached_enabled = False

        return ctx.rcon_cached_password, ctx.rcon_cached_port, ctx.rcon_cached_enabled


def is_rcon_enabled(ctx):
    """Return whether valid RCON credentials are currently configured."""
    _, _, enabled = refresh_rcon_config(ctx)
    return enabled


def run_mcrcon(ctx, command, timeout=4):
    """Execute an RCON command, trying compatible mcrcon argv variants."""
    password, port, enabled = refresh_rcon_config(ctx)
    if not enabled or not password:
        raise RuntimeError("RCON is disabled: rcon.password not found in server.properties")

    last_result = None
    for bin_path in candidate_mcrcon_bins():
        candidates = [
            [bin_path, "-H", ctx.RCON_HOST, "-P", str(port), "-p", password, command],
            [bin_path, "-H", ctx.RCON_HOST, "-p", password, command],
            [bin_path, "-p", password, command],
        ]
        for argv in candidates:
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                last_result = result
                if result.returncode == 0:
                    return result
            except Exception as exc:
                ctx.log_mcweb_exception("_run_mcrcon_candidate", exc)
                continue

    if last_result is not None:
        return last_result
    raise RuntimeError("mcrcon invocation failed")


def parse_players_online(output):
    """Parse player-count value from ``list`` command output."""
    text = clean_rcon_output(output).strip()
    if not text:
        return None
    match = re.search(r"There are\s+(\d+)\s+of a max of", text, re.IGNORECASE)
    if match:
        return match.group(1)
    if re.search(r"\bno players online\b", text, re.IGNORECASE):
        return "0"
    match = re.search(r"(\d+)\s+players?\s+online", text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"Players?\s+online:\s*(\d+)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def probe_tick_rate(ctx):
    """Probe server tick timing via RCON and normalize to milliseconds."""
    try:
        result = run_mcrcon(ctx, "forge tps", timeout=8)
    except Exception as exc:
        ctx.log_mcweb_exception("_probe_tick_rate", exc)
        return None
    if result.returncode != 0:
        return None

    output = clean_rcon_output((result.stdout or "") + (result.stderr or "")).strip()
    if not output:
        return None
    ms_match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*ms", output, re.IGNORECASE)
    if ms_match:
        try:
            ms_val = float(ms_match.group(1).replace(",", "."))
            if ms_val > 0:
                return f"{ms_val:.1f} ms"
        except ValueError:
            pass
    match = re.search(r"TPS[^0-9]*([0-9]+(?:[.,][0-9]+)?)", output, re.IGNORECASE)
    if match:
        try:
            tps = float(match.group(1).replace(",", "."))
            if tps > 0:
                return f"{(1000.0 / tps):.1f} ms"
        except ValueError:
            pass
    match = re.search(r"\b([0-9]+(?:[.,][0-9]+)?)\b", output)
    if match:
        try:
            tps = float(match.group(1).replace(",", "."))
            if 0 < tps <= 30:
                return f"{(1000.0 / tps):.1f} ms"
        except ValueError:
            pass
    return None


def probe_minecraft_runtime_metrics(ctx, force=False):
    """Probe and cache players/tick metrics with startup-safe fallbacks."""
    service_status = ctx.get_status()
    if service_status != "active":
        with ctx.mc_query_lock:
            ctx.mc_cached_players_online = "0" if service_status in ctx.OFF_STATES else "unknown"
            ctx.mc_cached_tick_rate = "--"
            ctx.mc_last_query_at = time.time()
        with ctx.rcon_startup_lock:
            ctx.rcon_startup_ready = False
        return ctx.mc_cached_players_online, ctx.mc_cached_tick_rate

    now = time.time()
    startup_ready = is_rcon_startup_ready(ctx, service_status=service_status)
    use_startup_fallback_probe = False
    if not startup_ready:
        session_started_at = ctx.read_session_start_time()
        startup_elapsed = None
        if session_started_at is not None:
            startup_elapsed = max(0.0, now - session_started_at)
        if startup_elapsed is not None and startup_elapsed >= ctx.RCON_STARTUP_FALLBACK_AFTER_SECONDS:
            use_startup_fallback_probe = True
        else:
            with ctx.mc_query_lock:
                ctx.mc_cached_players_online = "unknown"
                ctx.mc_cached_tick_rate = "--"
            return ctx.mc_cached_players_online, ctx.mc_cached_tick_rate

    # Shared cache lock prevents frequent concurrent probe storms.
    with ctx.mc_query_lock:
        probe_interval = ctx.MC_QUERY_INTERVAL_SECONDS
        if use_startup_fallback_probe:
            probe_interval = max(ctx.MC_QUERY_INTERVAL_SECONDS, ctx.RCON_STARTUP_FALLBACK_INTERVAL_SECONDS)
        if not force and (now - ctx.mc_last_query_at) < probe_interval:
            return ctx.mc_cached_players_online, ctx.mc_cached_tick_rate

    players_online = "unknown"
    tick_rate = "--"
    list_probe_ok = False
    try:
        list_result = run_mcrcon(ctx, "list", timeout=8)
        if list_result.returncode == 0:
            list_probe_ok = True
            parsed = parse_players_online((list_result.stdout or "") + (list_result.stderr or ""))
            if parsed is not None:
                players_online = parsed
    except Exception as exc:
        ctx.log_mcweb_exception("_probe_minecraft_runtime_metrics/list", exc)
    try:
        tick_rate_val = probe_tick_rate(ctx)
        if tick_rate_val:
            tick_rate = tick_rate_val
    except Exception as exc:
        ctx.log_mcweb_exception("_probe_minecraft_runtime_metrics/tps", exc)

    if use_startup_fallback_probe and (list_probe_ok or tick_rate != "--"):
        with ctx.rcon_startup_lock:
            ctx.rcon_startup_ready = True

    with ctx.mc_query_lock:
        ctx.mc_cached_players_online = players_online
        ctx.mc_cached_tick_rate = tick_rate
        ctx.mc_last_query_at = now
        return ctx.mc_cached_players_online, ctx.mc_cached_tick_rate


def get_players_online(ctx):
    """Return cached/probed players-online value."""
    players, _ = probe_minecraft_runtime_metrics(ctx)
    return players


def get_tick_rate(ctx):
    """Return cached/probed tick-rate value."""
    _, tick = probe_minecraft_runtime_metrics(ctx)
    return tick


def get_service_status_display(ctx, service_status, players_online):
    """Resolve user-facing service state from systemd + runtime intent/probes."""
    intent = ctx.get_service_status_intent()

    # Crash marker detection has highest priority until a new lifecycle action updates intent.
    if intent == "crashed":
        return "Crashed"

    # Rule 1: show Off when systemd says the service is off.
    if service_status in ("inactive", "failed"):
        ctx.set_service_status_intent(None)
        return "Off"

    # Transitional systemd states keep clear lifecycle labels.
    if service_status == "activating":
        return "Starting"
    if service_status == "deactivating":
        return "Shutting Down"

    # Active state: apply intent rules based on players and transient UI intent.
    if service_status == "active":
        players_is_integer = isinstance(players_online, str) and players_online.isdigit()

        # Rule 2: show Running when systemd is active and players is an integer.
        if players_is_integer:
            # Once players become resolvable, startup/shutdown transient intent is done.
            if intent in ("starting", "shutting"):
                ctx.set_service_status_intent(None)
            return "Running"

        # Rules 3 and 4: handle unknown player count with trigger intent.
        if intent == "shutting":
            return "Shutting Down"
        # Default unknown-on-active and explicit start intent both map to Starting.
        return "Starting"

    return "Off"


def get_service_status_class(service_status_display):
    """Map service display text to CSS class."""
    if service_status_display == "Running":
        return "stat-green"
    if service_status_display == "Starting":
        return "stat-yellow"
    if service_status_display == "Shutting Down":
        return "stat-orange"
    if service_status_display == "Crashed":
        return "stat-red"
    return "stat-red"

