from __future__ import annotations

import os
from pathlib import Path, PurePosixPath


def default_user_home(user_name=None):
    if user_name:
        return str(PurePosixPath("/Users") / str(user_name))
    return str(Path.home())


def default_minecraft_root(user_name=None):
    return str(PurePosixPath(default_user_home(user_name)) / "Minecraft")


def default_backup_dir(user_name=None):
    return str(PurePosixPath(default_user_home(user_name)) / "backups")


def is_valid_env_path(path_text):
    text = str(path_text or "").strip()
    if not text:
        return False
    if not text.startswith("/"):
        return False
    if ":" in text.split("/", 1)[0]:
        return False
    try:
        PurePosixPath(text)
        return True
    except Exception:
        return False


def normalize_for_host(path_text):
    return os.path.normpath(str(path_text or "").strip())
