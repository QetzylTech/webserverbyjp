"""Concrete infrastructure adapters implementing application ports."""

from __future__ import annotations

import subprocess
import shutil
import tempfile
import os
from pathlib import Path
from typing import Any

from app.core import state_store as state_store_service
from app.platform import get_calls, get_metrics, get_paths


class PlatformServiceControlAdapter:
    def __init__(self) -> None:
        self._calls = get_calls()
        self._paths = get_paths()

    def default_web_port(self) -> int:
        return int(self._calls.default_web_port())

    def default_minecraft_root(self, *, user_name: str = "") -> str:
        return str(self._paths.default_minecraft_root(user_name=user_name))

    def default_backup_dir(self, *, user_name: str = "") -> str:
        return str(self._paths.default_backup_dir(user_name=user_name))

    def resolve_backup_script_path(self, app_dir: Path | str) -> Path:
        return Path(self._paths.resolve_backup_script_path(app_dir))

    def apply_process_timezone(self, tz_name: str) -> None:
        self._calls.apply_process_timezone(tz_name)

    def is_valid_env_path(self, path_text: str) -> bool:
        return bool(self._paths.is_valid_env_path(path_text))

    def service_show_load_state(self, service_name: str, *, timeout: float = 5, minecraft_root: Any = None) -> Any:
        return self._calls.service_show_load_state(service_name, timeout=timeout, minecraft_root=minecraft_root)

    def service_is_active(self, service_name: str, *, timeout: float = 3, minecraft_root: Any = None) -> Any:
        return self._calls.service_is_active(service_name, timeout=timeout, minecraft_root=minecraft_root)

    def service_start_no_block(self, service_name: str, *, timeout: float = 12, minecraft_root: Any = None) -> Any:
        return self._calls.service_start_no_block(service_name, timeout=timeout, minecraft_root=minecraft_root)

    def service_start(self, service_name: str, *, timeout: float = 12, minecraft_root: Any = None) -> Any:
        return self._calls.service_start(service_name, timeout=timeout, minecraft_root=minecraft_root)

    def service_stop(self, service_name: str, *, timeout: float = 12, minecraft_root: Any = None) -> Any:
        return self._calls.service_stop(service_name, timeout=timeout, minecraft_root=minecraft_root)

    def run_elevated(self, cmd: list[str], *, timeout: float | None = None) -> Any:
        return self._calls.run_elevated(cmd, timeout=timeout)

    def run_mcrcon(self, host: str, port: int, password: str, command: str, *, timeout: float = 4) -> Any:
        return self._calls.run_mcrcon(host, port, password, command, timeout=timeout)

    def is_timeout_error(self, exc: BaseException) -> bool:
        return isinstance(exc, subprocess.TimeoutExpired)


class PlatformLogAdapter:
    def __init__(self) -> None:
        self._calls = get_calls()

    def minecraft_log_stream_mode(self) -> str:
        return str(self._calls.minecraft_log_stream_mode())

    def minecraft_load_recent_logs(self, service_name: str, logs_dir: Path, *, tail_lines: int = 1000, timeout: float = 4) -> str:
        return str(
            self._calls.minecraft_load_recent_logs(
                service_name,
                logs_dir,
                tail_lines=tail_lines,
                timeout=timeout,
            )
            or ""
        )

    def minecraft_startup_probe_output(self, service_name: str, logs_dir: Path, *, timeout: float = 4) -> str | None:
        value = self._calls.minecraft_startup_probe_output(service_name, logs_dir, timeout=timeout)
        if value is None:
            return None
        return str(value)

    def minecraft_follow_logs_command(self, service_name: str, logs_dir: Path) -> list[str] | None:
        cmd = self._calls.minecraft_follow_logs_command(service_name, logs_dir)
        if not cmd:
            return None
        return list(cmd)

    def minecraft_open_follow_logs_process(self, service_name: str, logs_dir: Path) -> Any | None:
        cmd = self.minecraft_follow_logs_command(service_name, logs_dir)
        if not cmd:
            return None
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    def iter_process_lines(self, process_handle: Any) -> Any:
        stdout = getattr(process_handle, "stdout", None)
        if stdout is None:
            return []
        return stdout

    def is_process_running(self, process_handle: Any) -> bool:
        try:
            return bool(process_handle is not None and process_handle.poll() is None)
        except Exception:
            return False

    def terminate_process(self, process_handle: Any) -> None:
        try:
            if self.is_process_running(process_handle):
                process_handle.terminate()
        except Exception:
            pass

    def is_timeout_error(self, exc: BaseException) -> bool:
        return isinstance(exc, subprocess.TimeoutExpired)


class PlatformBackupAdapter:
    def __init__(self) -> None:
        self._calls = get_calls()

    def run_backup_script(self, script_path: Path, trigger: str, *, timeout: float = 600) -> Any:
        return self._calls.run_backup_script(script_path, trigger, timeout=timeout)

    def is_timeout_error(self, exc: BaseException) -> bool:
        return isinstance(exc, subprocess.TimeoutExpired)


class PlatformMetricsAdapter:
    def __init__(self) -> None:
        self._metrics = get_metrics()

    def get_cpu_usage_per_core(self) -> str:
        return str(self._metrics.get_cpu_usage_per_core())

    def get_ram_usage(self) -> str:
        return str(self._metrics.get_ram_usage())

    def get_cpu_frequency(self) -> str:
        return str(self._metrics.get_cpu_frequency())

    def get_storage_usage(self) -> str:
        return str(self._metrics.get_storage_usage())


class StateStoreAdapter:
    """Thin adapter that forwards to state-store module functions."""

    def initialize_state_db(self, db_path: Path, log_exception: Any = None) -> Any:
        return state_store_service.initialize_state_db(db_path=db_path, log_exception=log_exception)

    def __getattr__(self, name: str) -> Any:
        target = getattr(state_store_service, name)
        if not callable(target):
            raise AttributeError(name)
        return target


class FilesystemAdapter:
    """Filesystem side-effect adapter."""

    def read_text(self, path: Path | str, *, encoding: str = "utf-8", errors: str = "strict") -> str:
        return Path(path).read_text(encoding=encoding, errors=errors)

    def write_text(self, path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
        Path(path).write_text(text, encoding=encoding)

    def ensure_dir(self, path: Path | str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def touch(self, path: Path | str) -> None:
        Path(path).touch(exist_ok=True)

    def move(self, src: Path | str, dst: Path | str) -> None:
        shutil.move(str(src), str(dst))

    def copy2(self, src: Path | str, dst: Path | str) -> None:
        shutil.copy2(str(src), str(dst))

    def copytree(self, src: Path | str, dst: Path | str) -> None:
        shutil.copytree(src, dst)

    def mkdtemp(self, *, prefix: str = "tmp") -> Path:
        return Path(tempfile.mkdtemp(prefix=prefix))

    def make_zip_archive(self, base_name: Path | str, *, root_dir: Path | str, base_dir: Path | str | None = None) -> Path:
        if base_dir is not None:
            return Path(
                shutil.make_archive(
                    str(base_name),
                    "zip",
                    root_dir=str(root_dir),
                    base_dir=str(base_dir),
                )
            )
        return Path(shutil.make_archive(str(base_name), "zip", root_dir=str(root_dir)))

    def rmtree(self, path: Path | str, *, ignore_errors: bool = False) -> None:
        shutil.rmtree(path, ignore_errors=ignore_errors)

    def disk_usage(self, path: Path | str) -> tuple[int, int, int]:
        usage = shutil.disk_usage(str(path))
        return int(usage.total), int(usage.used), int(usage.free)

    def can_write_dir(self, path: Path | str) -> bool:
        probe_dir = Path(path)
        if not probe_dir.exists() or not probe_dir.is_dir() or not os.access(str(probe_dir), os.W_OK):
            return False
        try:
            with tempfile.NamedTemporaryFile(dir=str(probe_dir), prefix=".mcweb_write_test_", delete=True):
                pass
            return True
        except Exception:
            return False
