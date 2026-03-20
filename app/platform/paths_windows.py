from __future__ import annotations

import re
from pathlib import PureWindowsPath


_DRIVE_ABS_RE = re.compile(r"^[A-Za-z]:[\\/].+")
_UNC_RE = re.compile(r"^\\\\[^\\]+\\[^\\]+")


def default_user_home(user_name=None):
    _ = user_name
    return r"C:\webserverbyjp"


def default_minecraft_root(user_name=None):
    _ = user_name
    return r"C:\webserverbyjp\Minecraft"


def default_backup_dir(user_name=None):
    _ = user_name
    return r"C:\webserverbyjp\backups"


def resolve_backup_script_path(app_dir):
    root = Path(str(app_dir))
    scripts_dir = root / "scripts"
    for name in ("backup.bat", "backup.cmd", "backup.ps1", "backup.sh"):
        candidate = scripts_dir / name
        if candidate.exists():
            return str(candidate)
    return str(scripts_dir / "backup.sh")


def is_valid_env_path(path_text):
    text = str(path_text or "").strip()
    if not text:
        return False
    if not (_DRIVE_ABS_RE.match(text) or _UNC_RE.match(text)):
        return False
    try:
        PureWindowsPath(text)
        return True
    except Exception:
        return False
