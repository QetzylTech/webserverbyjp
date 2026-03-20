"""Build world-path and static-asset helper callables for main.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_world_bindings(namespace: dict[str, Any]) -> dict[str, Any]:
    """Return world/static helper callables bound to runtime namespace."""
    ns = namespace

    def _read_level_name(server_properties_path: Path) -> str | None:
        try:
            lines = server_properties_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return None
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "level-name":
                value = value.strip()
                return value or None
        return None

    def _resolve_world_dir_from_server_properties() -> Path | None:
        for raw_candidate in ns["SERVER_PROPERTIES_CANDIDATES"]:
            candidate = Path(raw_candidate)
            if not candidate.exists():
                continue
            level_name = _read_level_name(candidate)
            if not level_name:
                continue
            path = Path(level_name)
            if path.is_absolute():
                return path
            return candidate.parent / path
        return None

    def _refresh_world_dir_from_server_properties() -> bool:
        resolved = _resolve_world_dir_from_server_properties()
        if resolved is None:
            return False
        ns["WORLD_DIR"] = resolved
        state = ns.get("STATE")
        if state is not None:
            try:
                state["WORLD_DIR"] = ns["WORLD_DIR"]
            except KeyError:
                pass
        return True

    def get_world_name() -> str:
        resolved = _resolve_world_dir_from_server_properties()
        if resolved is None:
            return "unknown"
        return resolved.name

    def _static_asset_version(filename: str) -> int:
        try:
            path = ns["APP_DIR"] / "static" / filename
            return int(getattr(path.stat(), "st_mtime_ns", int(path.stat().st_mtime * 1000000000)))
        except OSError:
            return 0

    return {
        "_read_level_name": _read_level_name,
        "_resolve_world_dir_from_server_properties": _resolve_world_dir_from_server_properties,
        "_refresh_world_dir_from_server_properties": _refresh_world_dir_from_server_properties,
        "get_world_name": get_world_name,
        "_static_asset_version": _static_asset_version,
    }

