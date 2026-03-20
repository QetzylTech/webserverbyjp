from __future__ import annotations

from pathlib import Path
import os
import subprocess
import tempfile
import shutil
from collections import deque


def _service_exists(service_name, *, timeout=5):
    result = subprocess.run(
        ["sc", "query", service_name],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode == 0


def _pid_file(service_name, minecraft_root=None):
    root_text = str(minecraft_root or "").strip()
    if root_text:
        try:
            root = Path(root_text)
            root.mkdir(parents=True, exist_ok=True)
            return root / f".mcweb_{service_name or 'minecraft'}_pid"
        except Exception:
            pass
    return Path(tempfile.gettempdir()) / f"mcweb_{service_name or 'minecraft'}_pid"


def _write_pid(service_name, pid, minecraft_root=None):
    path = _pid_file(service_name, minecraft_root)
    try:
        path.write_text(str(int(pid)), encoding="utf-8")
    except Exception:
        return


def _read_pid(service_name, minecraft_root=None):
    path = _pid_file(service_name, minecraft_root)
    try:
        text = path.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except Exception:
        return None


def _clear_pid(service_name, minecraft_root=None):
    path = _pid_file(service_name, minecraft_root)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        return


def _pid_is_running(pid, *, timeout=3):
    if pid is None:
        return False
    check = subprocess.run(
        ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    text = (check.stdout or "").strip().lower()
    if not text or "no tasks are running" in text:
        return False
    return str(int(pid)) in text


def run_elevated(cmd, *, timeout=None):
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def default_web_port():
    return 80


def apply_process_timezone(tz_name):
    os.environ["TZ"] = str(tz_name or "").strip()


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
    _ = timeout
    latest = _latest_log_file(logs_dir)
    if latest is None:
        return ""
    # Read a larger tail window so startup marker detection remains stable
    # after log rotation or delayed UI polling.
    return "\n".join(_tail_lines(latest, max_lines=400)).strip()


def minecraft_follow_logs_command(service_name, logs_dir):
    _ = service_name
    _ = logs_dir
    return None


def service_show_load_state(service_name, *, timeout=5, minecraft_root=None):
    if _service_exists(service_name, timeout=timeout):
        return subprocess.CompletedProcess(
            args=["sc", "query", service_name],
            returncode=0,
            stdout="loaded",
            stderr="",
        )
    run_script = Path(str(minecraft_root or "").strip() or "") / "run.bat"
    if run_script.exists() and run_script.is_file():
        return subprocess.CompletedProcess(
            args=["cmd", "/c", "run.bat"],
            returncode=0,
            stdout="loaded",
            stderr="",
        )
    return subprocess.CompletedProcess(
        args=["sc", "query", service_name],
        returncode=1,
        stdout="not-found",
        stderr="service not found",
    )


def service_is_active(service_name, *, timeout=3, minecraft_root=None):
    if _service_exists(service_name, timeout=timeout):
        result = subprocess.run(
            ["sc", "query", service_name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        text = (result.stdout or "").lower()
        if result.returncode == 0 and "running" in text:
            result.stdout = "active"
        else:
            result.stdout = "inactive"
        return result
    pid = _read_pid(service_name, minecraft_root)
    status = "active" if _pid_is_running(pid, timeout=timeout) else "inactive"
    if status == "inactive":
        _clear_pid(service_name, minecraft_root)
    return subprocess.CompletedProcess(
        args=["tasklist", "/FI", f"PID eq {int(pid) if pid else 0}"],
        returncode=0 if status == "active" else 1,
        stdout=status,
        stderr="",
    )


def _start_run_bat_no_block(service_name, *, timeout=12, minecraft_root=None):
    root_text = str(minecraft_root or "").strip()
    root = Path(root_text) if root_text else None
    if root is None or not root.exists() or not root.is_dir():
        return subprocess.CompletedProcess(
            args=["cmd", "/c", "run.bat"],
            returncode=1,
            stdout="",
            stderr="minecraft root not found",
        )
    run_script = root / "run.bat"
    if not run_script.exists() or not run_script.is_file():
        return subprocess.CompletedProcess(
            args=["cmd", "/c", "run.bat"],
            returncode=1,
            stdout="",
            stderr="run.bat not found",
        )
    existing_pid = _read_pid(service_name, root)
    if _pid_is_running(existing_pid, timeout=timeout):
        return subprocess.CompletedProcess(
            args=["cmd", "/c", "run.bat"],
            returncode=0,
            stdout="already running",
            stderr="",
        )

    try:
        start = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "$p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','run.bat' "
                f"-WorkingDirectory '{str(root)}' -PassThru -WindowStyle Hidden; "
                "$p.Id",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=["cmd", "/c", "run.bat"],
            returncode=1,
            stdout="",
            stderr="timed out starting run.bat",
        )

    if start.returncode != 0:
        return start
    try:
        pid = int((start.stdout or "").strip().splitlines()[-1])
    except Exception:
        pid = None
    if pid:
        _write_pid(service_name, pid, root)
    return start


def service_start_no_block(service_name, *, timeout=12, minecraft_root=None):
    if _service_exists(service_name, timeout=timeout):
        return run_elevated(["sc", "start", service_name], timeout=timeout)
    return _start_run_bat_no_block(
        service_name,
        timeout=timeout,
        minecraft_root=minecraft_root,
    )


def service_start(service_name, *, timeout=12, minecraft_root=None):
    return service_start_no_block(
        service_name,
        timeout=timeout,
        minecraft_root=minecraft_root,
    )


def service_stop(service_name, *, timeout=12, minecraft_root=None):
    if _service_exists(service_name, timeout=timeout):
        return run_elevated(["sc", "stop", service_name], timeout=timeout)
    pid = _read_pid(service_name, minecraft_root)
    if pid is None:
        return subprocess.CompletedProcess(
            args=["taskkill", "/PID", "0", "/T", "/F"],
            returncode=0,
            stdout="not running",
            stderr="",
        )
    result = subprocess.run(
        ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode == 0:
        _clear_pid(service_name, minecraft_root)
    return result


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

    # Prefer shell interpreters for .sh scripts on Windows.
    if script.suffix.lower() == ".sh":
        shell_candidates = []
        for name in ("bash", "sh"):
            found = shutil.which(name)
            if found:
                shell_candidates.append(found)
        for fixed in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ):
            if Path(fixed).exists():
                shell_candidates.append(fixed)
        seen = set()
        for shell_path in shell_candidates:
            key = str(shell_path).lower()
            if key in seen:
                continue
            seen.add(key)
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
