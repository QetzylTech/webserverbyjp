"""Minecraft RCON probing use cases."""

import re
import time
from typing import Any

from app.ports import ports


def is_rcon_startup_ready(ctx: Any, service_status: str | None = None) -> bool:
    if service_status is None:
        service_status = ctx.get_status()
    if service_status != "active":
        with ctx.rcon_startup_lock:
            ctx.rcon_startup_ready = False
        return False
    with ctx.rcon_startup_lock:
        if ctx.rcon_startup_ready:
            return True
    intent = str(ctx.get_service_status_intent() or "").strip().lower()
    if intent == "starting":
        return False
    with ctx.rcon_startup_lock:
        ctx.rcon_startup_ready = True
    return True


def clean_rcon_output(text: object) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", cleaned)
    cleaned = re.sub(r"\u00a7.", "", cleaned)
    return cleaned


def refresh_rcon_config(ctx: Any) -> tuple[str | None, int | None, bool]:
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
            port_text = str(kv.get("rcon.port", "") or "").strip()
            if port_text.isdigit():
                parsed_port = int(port_text)
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


def is_rcon_enabled(ctx: Any) -> bool:
    _, _, enabled = refresh_rcon_config(ctx)
    return enabled


def run_mcrcon(ctx: Any, command: str, timeout: float = 4) -> Any:
    password, port, enabled = refresh_rcon_config(ctx)
    if not enabled or not password:
        raise RuntimeError("RCON is disabled: rcon.password not found in server.properties")
    resolved_port = int(port or getattr(ctx, "RCON_PORT", 25575) or 25575)
    try:
        return ports.service_control.run_mcrcon(ctx.RCON_HOST, resolved_port, password, command, timeout=timeout)
    except Exception as exc:
        ctx.log_mcweb_exception("_run_mcrcon", exc)
        raise RuntimeError("mcrcon invocation failed") from exc


def parse_players_online(output: object) -> str | None:
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


def probe_tick_rate(ctx: Any) -> str | None:
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


def probe_minecraft_runtime_metrics(ctx: Any, force: bool = False) -> tuple[str, str]:
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
    if not startup_ready:
        with ctx.mc_query_lock:
            intent = ctx.get_service_status_intent()
            if intent == "starting":
                ctx.mc_cached_players_online = "unknown"
            else:
                cached_players = str(ctx.mc_cached_players_online or "")
                ctx.mc_cached_players_online = cached_players if cached_players.isdigit() else "0"
            ctx.mc_cached_tick_rate = "--"
        return ctx.mc_cached_players_online, ctx.mc_cached_tick_rate
    with ctx.mc_query_lock:
        probe_interval = ctx.MC_QUERY_INTERVAL_SECONDS
        if not force and (now - ctx.mc_last_query_at) < probe_interval:
            return ctx.mc_cached_players_online, ctx.mc_cached_tick_rate
    players_online = "unknown"
    tick_rate = "--"
    try:
        list_result = run_mcrcon(ctx, "list", timeout=8)
        if list_result.returncode == 0:
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
    with ctx.mc_query_lock:
        if players_online == "unknown":
            cached_players = str(ctx.mc_cached_players_online or "")
            if cached_players.isdigit():
                players_online = cached_players
            elif ctx.get_service_status_intent() != "starting":
                players_online = "0"
        ctx.mc_cached_players_online = players_online
        ctx.mc_cached_tick_rate = tick_rate
        ctx.mc_last_query_at = now
        return ctx.mc_cached_players_online, ctx.mc_cached_tick_rate


def get_players_online(ctx: Any) -> str:
    players, _ = probe_minecraft_runtime_metrics(ctx)
    return players


def get_tick_rate(ctx: Any) -> str:
    _, tick = probe_minecraft_runtime_metrics(ctx)
    return tick
