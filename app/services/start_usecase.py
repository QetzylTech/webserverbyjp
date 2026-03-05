"""Start/session control-plane use cases."""

import time
from types import SimpleNamespace

from werkzeug.security import check_password_hash

from app.ports import ports
from app.services.restore_workflow_helpers import ensure_session_file, ensure_startup_rcon_settings

_calls = SimpleNamespace(
    service_start_no_block=ports.service_control.service_start_no_block,
)


def set_service_status_intent(ctx, intent):
    normalized_intent = str(intent or "").strip().lower()
    if normalized_intent == "starting":
        with ctx.rcon_startup_lock:
            ctx.rcon_startup_ready = False
    with ctx.service_status_intent_lock:
        ctx.service_status_intent = intent
    if normalized_intent == "starting":
        try:
            ctx.ensure_log_stream_fetcher_started("minecraft")
        except Exception as exc:
            ctx.log_mcweb_exception("set_service_status_intent/start_log_fetcher", exc)


def get_service_status_intent(ctx):
    with ctx.service_status_intent_lock:
        return ctx.service_status_intent


def validate_sudo_password(ctx, sudo_password):
    expected_hash = (getattr(ctx, "ADMIN_PASSWORD_HASH", "") or "").strip()
    candidate = (sudo_password or "").strip()
    if not expected_hash or not candidate:
        return False
    try:
        return bool(check_password_hash(expected_hash, candidate))
    except ValueError:
        return False


def read_session_start_time(ctx):
    if not ensure_session_file(ctx):
        return None
    try:
        raw = ctx.session_state.session_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        ts = float(raw)
    except ValueError:
        return None
    if ts <= 0:
        return None
    if ts > 1_000_000_000_000:
        ts = ts / 1000.0
    return ts


def get_session_start_time(ctx, service_status=None):
    if service_status is None:
        service_status = ctx.get_status()
    if service_status in ctx.OFF_STATES:
        return None
    return read_session_start_time(ctx)


def get_session_duration_text(ctx):
    start_time = read_session_start_time(ctx)
    if start_time is None:
        return "--"
    elapsed = max(0, int(time.time() - start_time))
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def start_service_non_blocking(ctx, timeout=12):
    rcon_result = ensure_startup_rcon_settings(ctx)
    if not rcon_result.get("ok"):
        return {
            "ok": False,
            "message": rcon_result.get("message", "Failed to enforce startup RCON settings."),
        }
    try:
        result = _calls.service_start_no_block(
            ctx.SERVICE,
            timeout=timeout,
            minecraft_root=ctx.MINECRAFT_ROOT_DIR,
        )
    except Exception as exc:
        if not ports.service_control.is_timeout_error(exc):
            raise
        return {"ok": False, "message": "Failed to start service: timed out issuing non-blocking start."}
    if result.returncode != 0:
        detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
        message = "Failed to start service."
        if detail:
            message = f"Failed to start service: {detail[:400]}"
        return {"ok": False, "message": message}
    ctx.invalidate_status_cache()
    return {"ok": True, "message": ""}
