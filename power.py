#!/usr/bin/env python3
import subprocess
import time

SERVICE = "minecraft.service"
POLL_SECONDS = 5

def run(cmd):
    return subprocess.run(cmd, check=False, capture_output=True, text=True)

def service_active():
    r = run(["systemctl", "is-active", SERVICE])
    return r.stdout.strip() == "active"

def set_governor(mode):
    # mode: performance or powersave
    # Requires cpupower package and root privileges
    run(["cpupower", "frequency-set", "-g", mode])

def main():
    last = None
    while True:
        active = service_active()
        target = "performance" if active else "powersave"
        if target != last:
            set_governor(target)
            print(f"{SERVICE}: {'ON' if active else 'OFF'} -> governor={target}")
            last = target
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
