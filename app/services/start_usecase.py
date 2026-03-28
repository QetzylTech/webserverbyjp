"""Start/session control-plane use cases."""

import time
from types import SimpleNamespace
from typing import Any, cast

from werkzeug.security import check_password_hash

from app.services import notification_service as notification_service
from app.services import password_throttle as password_throttle_service

from app.ports import ports
from app.services.restore_workflow_helpers import ensure_session_file, ensure_startup_rcon_settings

_calls = SimpleNamespace(
    service_start_no_block=ports.service_control.service_start_no_block,
)
_notification_service = cast(Any, notification_service)
_password_throttle_service = cast(Any, password_throttle_service)
_ensure_session_file = cast(Any, ensure_session_file)
_ensure_startup_rcon_settings = cast(Any, ensure_startup_rcon_settings)


def set_service_status_intent(ctx: Any, intent: object) -> None:
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


def get_service_status_intent(ctx: Any) -> object:
    with ctx.service_status_intent_lock:
        return ctx.service_status_intent


def _password_required(ctx: Any) -> bool:
    try:
        return bool(getattr(ctx, "REQUIRE_SUDO_PASSWORD", True))
    except Exception:
        return True


def _client_ip(ctx: Any) -> str:
    client_ip = ""
    getter = getattr(ctx, "_get_client_ip", None)
    if callable(getter):
        try:
            client_ip = str(getter() or "").strip()
        except Exception:
            client_ip = ""
    return client_ip


def _publish_password_throttle(ctx: Any, blocked_until: float) -> None:
    retry_seconds = max(0, int(blocked_until - time.time()))
    _notification_service.publish_ui_notification(
        ctx,
        {
            "code": "password_throttle",
            "kind": "warning",
            "message": f"Password retries paused for {retry_seconds} seconds after 3 failed attempts.",
            "retry_after_seconds": retry_seconds,
        },
    )
    try:
        ctx.log_mcweb_action(
            "password_throttle",
            rejection_message=f"Password retries paused for {retry_seconds} seconds.",
        )
    except Exception:
        pass


def _validate_password_hash(ctx: Any, sudo_password: object, expected_hash: object) -> bool:
    client_ip = _client_ip(ctx)
    if _password_throttle_service.is_blocked(ctx, client_ip):
        return False

    expected = str(expected_hash or "").strip()
    candidate = str(sudo_password or "").strip()
    if not expected or not candidate:
        blocked_until, triggered = _password_throttle_service.record_failure(ctx, client_ip)
        if triggered:
            _publish_password_throttle(ctx, blocked_until)
        return False
    try:
        ok = bool(check_password_hash(expected, candidate))
    except ValueError:
        ok = False
    if ok:
        _password_throttle_service.record_success(ctx, client_ip)
        return True
    blocked_until, triggered = _password_throttle_service.record_failure(ctx, client_ip)
    if triggered:
        _publish_password_throttle(ctx, blocked_until)
    return False


def _admin_password_hash(ctx: Any) -> str:
    return str(getattr(ctx, "ADMIN_PASSWORD_HASH", "") or "").strip()


def _superadmin_password_hash(ctx: Any) -> str:
    return str(getattr(ctx, "SUPERADMIN_PASSWORD_HASH", "") or "").strip() or _admin_password_hash(ctx)


def validate_sudo_password(ctx: Any, sudo_password: object) -> bool:
    if not _password_required(ctx):
        return True
    return _validate_password_hash(ctx, sudo_password, _admin_password_hash(ctx))


def validate_admin_password(ctx: Any, sudo_password: object) -> bool:
    return _validate_password_hash(ctx, sudo_password, _admin_password_hash(ctx))


def validate_superadmin_password(ctx: Any, sudo_password: object) -> bool:
    return _validate_password_hash(ctx, sudo_password, _superadmin_password_hash(ctx))


def read_session_start_time(ctx: Any) -> float | None:
    if not _ensure_session_file(ctx):
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


def get_session_start_time(ctx: Any, service_status: object = None) -> float | None:
    if service_status is None:
        service_status = ctx.get_status()
    if service_status in ctx.OFF_STATES:
        return None
    return read_session_start_time(ctx)


def get_session_duration_text(ctx: Any) -> str:
    start_time = read_session_start_time(ctx)
    if start_time is None:
        return "--"
    elapsed = max(0, int(time.time() - start_time))
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def start_service_non_blocking(ctx: Any, timeout: int = 12) -> dict[str, object]:
    rcon_result = _ensure_startup_rcon_settings(ctx)
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
