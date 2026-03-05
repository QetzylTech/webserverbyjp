from __future__ import annotations

import subprocess
import shutil
from pathlib import Path
from collections import deque


def run_elevated(cmd, *, timeout=None):
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def default_web_port():
    return 8080


def _latest_log_file(logs_dir):
    base = Path(str(logs_dir or "").strip() or "")
    if not base.exists() or not base.is_dir():
        return None
    direct = base / "latest.log"
    if direct.exists() and direct.is_file():
        return direct
    try:
        candidates = [p for p in base.glob("*.log") if p.is_file()]
    except OSError:
        candidates = []
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda p: p.stat().st_mtime_ns)
    except OSError:
        return None


def _tail_lines(path, max_lines):
    keep = max(1, int(max_lines or 1))
    bucket = deque(maxlen=keep)
    try:
        with Path(path).open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                bucket.append(line.rstrip("\r\n"))
    except OSError:
        return []
    return list(bucket)


def minecraft_log_stream_mode():
    return "file_poll"


def minecraft_load_recent_logs(service_name, logs_dir, *, tail_lines=1000, timeout=4):
    _ = service_name
    _ = timeout
    latest = _latest_log_file(logs_dir)
    if latest is None:
        return ""
    return "\n".join(_tail_lines(latest, max_lines=tail_lines)).strip()


def minecraft_startup_probe_output(service_name, logs_dir, *, timeout=4):
    _ = service_name
    _ = logs_dir
    _ = timeout
    return None


def minecraft_follow_logs_command(service_name, logs_dir):
    _ = service_name
    _ = logs_dir
    return None


def service_show_load_state(service_name, *, timeout=5, minecraft_root=None):
    _ = minecraft_root
    result = subprocess.run(
        ["launchctl", "print", f"system/{service_name}"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode == 0:
        result.stdout = "loaded"
    else:
        result.stdout = "not-found"
    return result


def service_is_active(service_name, *, timeout=3, minecraft_root=None):
    _ = minecraft_root
    result = subprocess.run(
        ["launchctl", "print", f"system/{service_name}"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode == 0:
        result.stdout = "active"
    else:
        result.stdout = "inactive"
    return result


def service_start_no_block(service_name, *, timeout=12, minecraft_root=None):
    _ = minecraft_root
    return run_elevated(["launchctl", "kickstart", "-k", f"system/{service_name}"], timeout=timeout)


def service_start(service_name, *, timeout=12, minecraft_root=None):
    _ = minecraft_root
    return run_elevated(["launchctl", "kickstart", "-k", f"system/{service_name}"], timeout=timeout)


def service_stop(service_name, *, timeout=12, minecraft_root=None):
    _ = minecraft_root
    return run_elevated(["launchctl", "stop", f"system/{service_name}"], timeout=timeout)


def run_mcrcon(host, port, password, command, *, timeout=4):
    return subprocess.run(
        ["mcrcon", "-H", str(host), "-P", str(port), "-p", str(password), str(command)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_backup_script(script_path, trigger, *, timeout=600):
    script = Path(str(script_path))
    cwd = str(script.parent) if script.parent else None
    trigger_text = str(trigger)
    if script.suffix.lower() == ".sh":
        for name in ("bash", "sh"):
            shell_path = shutil.which(name)
            if not shell_path:
                continue
            try:
                return subprocess.run(
                    [str(shell_path), str(script), trigger_text],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=cwd,
                )
            except FileNotFoundError:
                continue
    return subprocess.run(
        [str(script), trigger_text],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
