"""Minimal KEY=VALUE config loader with typed accessors."""

from pathlib import Path


class WebConfig:
    """Read a dotenv-like config file and expose typed getters."""

    def __init__(self, config_path, base_dir):
        self.config_path = Path(config_path)
        self.base_dir = Path(base_dir)
        self.values = self._load()

    def _load(self):
        """Parse config lines and return a key/value mapping."""
        values = {}
        try:
            lines = self.config_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return values
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
        return values

    def get_str(self, name, default):
        """Read a string setting and fall back when missing/blank."""
        value = self.values.get(name)
        if value is None:
            return default
        value = value.strip()
        return value if value else default

    def get_int(self, name, default, minimum=None):
        """Read an integer setting with optional lower-bound clamping."""
        raw = self.values.get(name)
        if raw is None:
            return default
        try:
            parsed = int(str(raw).strip())
        except (TypeError, ValueError):
            return default
        if minimum is not None and parsed < minimum:
            return minimum
        return parsed

    def get_float(self, name, default, minimum=None):
        """Read a float setting with optional lower-bound clamping."""
        raw = self.values.get(name)
        if raw is None:
            return default
        try:
            parsed = float(str(raw).strip())
        except (TypeError, ValueError):
            return default
        if minimum is not None and parsed < minimum:
            return minimum
        return parsed

    def get_path(self, name, default):
        """Read a path setting and resolve relative values from ``base_dir``."""
        raw = self.values.get(name)
        if raw is None:
            return Path(default)
        candidate = Path(str(raw).strip())
        if not str(candidate):
            return Path(default)
        if candidate.is_absolute():
            return candidate
        return self.base_dir / candidate
