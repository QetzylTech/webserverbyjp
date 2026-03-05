from __future__ import annotations

import subprocess


def run_elevated(cmd, *, timeout=None):
    return subprocess.run(
        ["sudo", "-n"] + list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def service_show_load_state(service_name, *, timeout=5):
    return subprocess.run(
        ["systemctl", "show", service_name, "--property=LoadState", "--value"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def service_is_active(service_name, *, timeout=3):
    return subprocess.run(
        ["systemctl", "is-active", service_name],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def service_start_no_block(service_name, *, timeout=12):
    return run_elevated(["systemctl", "start", "--no-block", service_name], timeout=timeout)


def service_start(service_name, *, timeout=12):
    return run_elevated(["systemctl", "start", service_name], timeout=timeout)


def service_stop(service_name, *, timeout=12):
    return run_elevated(["systemctl", "stop", service_name], timeout=timeout)


def run_mcrcon(host, port, password, command, *, timeout=4):
    return subprocess.run(
        ["mcrcon", "-H", str(host), "-P", str(port), "-p", str(password), str(command)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_backup_script(script_path, trigger, *, timeout=600):
    return subprocess.run(
        [str(script_path), str(trigger)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
