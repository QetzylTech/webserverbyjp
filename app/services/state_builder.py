"""Explicit AppState builder."""

from app.state import AppState


def assert_required_keys_present(bindings):
    """Raise when any required AppState members are missing from bindings."""
    required = AppState.__annotations__.keys()
    missing = [key for key in required if key not in bindings]
    if missing:
        raise KeyError(f"Missing state members: {', '.join(missing)}")


def build_app_state(bindings):
    """Build AppState from explicit bindings using annotated keys."""
    assert_required_keys_present(bindings)
    required = AppState.__annotations__.keys()
    data = {}
    for key in required:
        data[key] = bindings[key]
    return AppState(**data)
