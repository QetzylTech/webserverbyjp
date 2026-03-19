"""Shared helper functions for restore workflow operations."""

from datetime import datetime
from pathlib import Path
import os
import re
import secrets
import time
import uuid

from app.core import state_store as state_store_service
from app.ports import ports

def run_sudo(ctx, cmd):
    """Run command via non-interactive sudo."""
    return ports.service_control.run_elevated(cmd)


def stop_service_systemd(ctx):
    """Stop the systemd service and wait briefly for an off-state."""
    try:
        ports.service_control.service_stop(
            ctx.SERVICE,
            timeout=12,
            minecraft_root=ctx.MINECRAFT_ROOT_DIR,
        )
        ctx.invalidate_status_cache()
    except Exception as exc:
        ctx.log_mcweb_exception("stop_service_systemd", exc)

    wait_seconds = float(getattr(ctx, "OPERATION_STOP_TIMEOUT_SECONDS", 30.0) or 30.0)
    wait_seconds = max(10.0, min(wait_seconds, 120.0))
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if ctx.get_status() in ctx.OFF_STATES:
            return True
        time.sleep(0.5)
    return ctx.get_status() in ctx.OFF_STATES


def start_service(ctx):
    """Start the configured service and return command result object."""
    return ports.service_control.service_start(
        ctx.SERVICE,
        timeout=12,
        minecraft_root=ctx.MINECRAFT_ROOT_DIR,
    )


def ensure_session_file(ctx):
    """Ensure the session tracking file exists and is writable."""
    try:
        session_file = ctx.session_state.session_file
        ports.filesystem.ensure_dir(session_file.parent)
        ports.filesystem.touch(session_file)
        return True
    except OSError:
        return False


def write_session_start_time(ctx, timestamp=None):
    """Write session start epoch seconds and return the stored value."""
    if not ensure_session_file(ctx):
        return None
    ts = time.time() if timestamp is None else float(timestamp)
    try:
        ports.filesystem.write_text(ctx.session_state.session_file, f"{ts:.6f}\n", encoding="utf-8")
    except OSError:
        return None
    return ts


def clear_session_start_time(ctx):
    """Clear the session tracking file."""
    if not ensure_session_file(ctx):
        return False
    try:
        ports.filesystem.write_text(ctx.session_state.session_file, "", encoding="utf-8")
    except OSError:
        return False
    return True


def reset_backup_schedule_state(ctx):
    """Reset periodic backup run counter for current session."""
    with ctx.backup_state.lock:
        ctx.backup_state.periodic_runs = 0


def is_backup_running(ctx, include_run_lock=True):
    """Return whether backup script reports active run via state file."""
    if include_run_lock:
        backup_state = getattr(ctx, "backup_state", None)
        run_lock = getattr(backup_state, "run_lock", None)
        if run_lock is not None:
            try:
                if bool(run_lock.locked()):
                    return True
            except Exception:
                pass
    try:
        state_path = Path(ctx.BACKUP_STATE_FILE)
        ports.filesystem.ensure_dir(state_path.parent)
        raw = ports.filesystem.read_text(state_path, encoding="utf-8").strip().lower()
    except OSError:
        return False
    if raw != "true":
        return False
    stale_seconds = float(getattr(ctx, "BACKUP_STATE_STALE_SECONDS", 1200.0) or 1200.0)
    if stale_seconds > 0:
        try:
            mtime = state_path.stat().st_mtime
            age = time.time() - float(mtime)
            if age > stale_seconds:
                ports.filesystem.write_text(state_path, "false\n", encoding="utf-8")
                try:
                    ctx.log_mcweb_log(
                        "backup-state-stale",
                        command=f"age={age:.1f}s",
                        rejection_message="Cleared stale backup running flag.",
                    )
                except Exception:
                    pass
                return False
        except Exception:
            pass
    return True

def _restore_failed(message, error="restore_failed"):
    """Return normalized restore failure payload."""
    return {"ok": False, "error": error, "message": message}


def _detect_server_properties_path(ctx):
    """Return first server.properties path candidate that exists."""
    for path in ctx.SERVER_PROPERTIES_CANDIDATES:
        candidate = Path(path)
        if candidate.exists():
            return candidate
    return None


def _parse_server_properties_kv(text):
    """Parse KEY=VALUE lines from server.properties style content."""
    kv = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        kv[key.strip()] = value.strip()
    return kv


def _update_property_text(original_text, key, value):
    """Replace/add one server.properties key assignment in text."""
    lines = original_text.splitlines()
    target = f"{key}="
    found = False
    out = []
    for line in lines:
        if line.startswith(target):
            out.append(f"{target}{value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{target}{value}")
    return "\n".join(out) + "\n"


def ensure_startup_rcon_settings(ctx):
    """Ensure startup RCON settings are present and rotate password each start."""
    props_path = _detect_server_properties_path(ctx)
    if props_path is None:
        return {"ok": False, "message": "server.properties not found."}
    try:
        original_text = ports.filesystem.read_text(props_path, encoding="utf-8", errors="ignore")
    except OSError:
        return {"ok": False, "message": "Failed to read server.properties."}

    kv = _parse_server_properties_kv(original_text)
    port_value = str(kv.get("rcon.port", "") or "").strip()
    if not port_value.isdigit():
        port_value = "25575"

    password_value = secrets.token_urlsafe(32)

    updated = original_text
    updated = _update_property_text(updated, "enable-rcon", "true")
    updated = _update_property_text(updated, "rcon.port", port_value)
    updated = _update_property_text(updated, "rcon.password", password_value)
    try:
        ports.filesystem.write_text(props_path, updated, encoding="utf-8")
    except OSError:
        return {"ok": False, "message": "Failed to write server.properties."}

    try:
        ctx._refresh_rcon_config()
    except Exception:
        pass

    return {"ok": True, "path": str(props_path), "rcon_port": port_value}


def _record_restore_history(ctx, backup_name, old_world_dir, archived_old_world_dir, new_world_dir):
    """Append restore world switch reference to data/restore.history."""
    try:
        data_dir = Path(ctx.session_state.session_file).parent
        ports.filesystem.ensure_dir(data_dir)
        log_file = data_dir / "restore.history"
        stamp = datetime.now(tz=ctx.DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        line = (
            f"{stamp} | backup={backup_name} | old={old_world_dir} "
            f"| archived={archived_old_world_dir} | new={new_world_dir}\n"
        )
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return True
    except OSError:
        return False


def _sanitize_backup_name_component(value):
    """Sanitize filename component for backup/pre-restore artifact names."""
    safe = re.sub(r"[^A-Za-z0-9(). _-]+", "_", str(value or "")).strip()
    return safe or "world"


_RESTORE_WORLD_NAME_MAX_LEN = 32
_RESTORE_ID_BODY_LEN = 5
_RESTORE_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _normalize_world_base_name(value):
    """Normalize a world-name base by collapsing separators."""
    text = str(value or "").strip()
    if not text:
        return "World"
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or "World"


def _derive_restore_base_name(backup_filename, restore_source):
    """Derive a readable base name from selected backup filename and extracted source."""
    stem = Path(str(backup_filename or "")).stem.strip()
    stem = re.sub(
        r"(?i)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_pre_restore|_prerestore)?$",
        "",
        stem,
    )
    stem = re.sub(r"(?i)(?:_pre_restore|_prerestore)$", "", stem)
    normalized = _normalize_world_base_name(stem)
    if normalized and normalized.lower() != "world":
        return normalized
    if restore_source is not None:
        source_name = _normalize_world_base_name(getattr(restore_source, "name", ""))
        if source_name:
            return source_name
    return normalized


def _compose_restore_world_name(base_name, prefix, code):
    """Build a level-name suffixing with (Gx<id>) or (Rx<id>) and enforce 32-char max."""
    normalized = _normalize_world_base_name(base_name)
    suffix = f" ({prefix}{code})"
    keep = max(1, _RESTORE_WORLD_NAME_MAX_LEN - len(suffix))
    trimmed = normalized[:keep].rstrip(" ._-()")
    if not trimmed:
        trimmed = "World"[:keep]
    return f"{trimmed}{suffix}"


def _new_restore_code(ctx):
    """Generate a unique 5-char alphanumeric restore code tracked in SQLite."""
    db_path = Path(ctx.APP_STATE_DB_PATH)
    for _ in range(128):
        code = "".join(secrets.choice(_RESTORE_ID_ALPHABET) for _ in range(_RESTORE_ID_BODY_LEN))
        if not state_store_service.restore_id_exists(db_path, code):
            return code
    return uuid.uuid4().hex[:_RESTORE_ID_BODY_LEN]


def _archive_old_world_dir(ctx, old_world_dir, archived_world_name, *, progress=None):
    """Move previous world directory to data/old_worlds and return destination."""
    data_dir = Path(ctx.session_state.session_file).parent
    old_worlds_dir = data_dir / "old_worlds"
    try:
        ports.filesystem.ensure_dir(old_worlds_dir)
    except OSError:
        return None, "Failed to create old_worlds archive directory."

    base_name = str(archived_world_name or old_world_dir.name).strip() or old_world_dir.name
    archived_old_world_dir = old_worlds_dir / base_name
    suffix = 1
    while archived_old_world_dir.exists():
        archived_old_world_dir = old_worlds_dir / f"{base_name}_{suffix}"
        suffix += 1

    def emit(message):
        if not progress:
            return
        try:
            progress(message)
        except Exception:
            pass

    emit(f"Archiving world dir: {old_world_dir} -> {archived_old_world_dir}")
    try:
        for root, dirs, files in os.walk(old_world_dir):
            root_path = Path(root)
            rel_root = root_path.relative_to(old_world_dir)
            dest_root = archived_old_world_dir / rel_root
            emit(f"Archive dir: {dest_root}")
            for dirname in dirs:
                emit(f"Archive dir: {dest_root / dirname}")
            for filename in files:
                emit(f"Archive file: {dest_root / filename}")
    except Exception:
        pass

    try:
        ports.filesystem.move(old_world_dir, archived_old_world_dir)
    except Exception:
        return None, "Failed to archive previous world directory."
    return archived_old_world_dir, ""


def _restore_source_from_extraction(ctx, extract_root):
    """Resolve the extracted world root directory from a backup zip."""
    expected_abs = str(ctx.WORLD_DIR).lstrip("/\\")
    candidates = [
        extract_root / expected_abs,
        extract_root / ctx.WORLD_DIR.name,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    if (extract_root / "level.dat").exists():
        return extract_root

    children = [p for p in extract_root.iterdir() if p.is_dir()]
    if len(children) == 1:
        return children[0]
    return None

