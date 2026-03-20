from __future__ import annotations

from pathlib import Path, PurePosixPath


def default_user_home(*, user_name=None, users_root="/home"):
    if user_name:
        return str(PurePosixPath(str(users_root)) / str(user_name))
    return str(Path.home())


def default_minecraft_root(*, user_name=None, users_root="/home"):
    return str(PurePosixPath(default_user_home(user_name=user_name, users_root=users_root)) / "Minecraft")


def default_backup_dir(*, user_name=None, users_root="/home"):
    return str(PurePosixPath(default_user_home(user_name=user_name, users_root=users_root)) / "backups")


def resolve_backup_script_path(app_dir):
    root = Path(str(app_dir))
    scripts_dir = root / "scripts"
    for name in ("backup.sh", "backup.command", "backup"):
        candidate = scripts_dir / name
        if candidate.exists():
            return str(candidate)
    return str(scripts_dir / "backup.sh")


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
