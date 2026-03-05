from __future__ import annotations

import subprocess
import shutil
from pathlib import Path


def run_elevated(cmd, *, timeout=None):
    return subprocess.run(
        ["sudo", "-n"] + list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def default_web_port():
    return 8080


def minecraft_log_stream_mode():
    return "journal"


def minecraft_load_recent_logs(service_name, logs_dir, *, tail_lines=1000, timeout=4):
    _ = logs_dir
    result = subprocess.run(
        ["journalctl", "-u", service_name, "-n", str(int(tail_lines)), "--no-pager"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return ((result.stdout or "") + (result.stderr or "")).strip()


def minecraft_startup_probe_output(service_name, logs_dir, *, timeout=4):
    _ = logs_dir
    result = subprocess.run(
        ["journalctl", "-u", service_name, "-n", "500", "--no-pager"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return ((result.stdout or "") + (result.stderr or "")).strip()


def minecraft_follow_logs_command(service_name, logs_dir):
    _ = logs_dir
    return ["journalctl", "-u", service_name, "-f", "-n", "0", "--no-pager"]


def service_show_load_state(service_name, *, timeout=5, minecraft_root=None):
    _ = minecraft_root
    return subprocess.run(
        ["systemctl", "show", service_name, "--property=LoadState", "--value"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def service_is_active(service_name, *, timeout=3, minecraft_root=None):
    _ = minecraft_root
    return subprocess.run(
        ["systemctl", "is-active", service_name],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def service_start_no_block(service_name, *, timeout=12, minecraft_root=None):
    _ = minecraft_root
    return run_elevated(["systemctl", "start", "--no-block", service_name], timeout=timeout)


def service_start(service_name, *, timeout=12, minecraft_root=None):
    _ = minecraft_root
    return run_elevated(["systemctl", "start", service_name], timeout=timeout)


def service_stop(service_name, *, timeout=12, minecraft_root=None):
    _ = minecraft_root
    return run_elevated(["systemctl", "stop", service_name], timeout=timeout)


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
