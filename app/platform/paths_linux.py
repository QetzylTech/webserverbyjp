from __future__ import annotations

from app.platform import paths_posix as _posix


def default_user_home(user_name=None):
    return _posix.default_user_home(user_name=user_name, users_root="/home")


def default_minecraft_root(user_name=None):
    return _posix.default_minecraft_root(user_name=user_name, users_root="/home")


def default_backup_dir(user_name=None):
    return _posix.default_backup_dir(user_name=user_name, users_root="/home")


def resolve_backup_script_path(app_dir):
    return _posix.resolve_backup_script_path(app_dir)


def is_valid_env_path(path_text):
    return _posix.is_valid_env_path(path_text)
