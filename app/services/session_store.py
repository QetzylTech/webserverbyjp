"""Session store/service wrappers."""
def ensure_session_file(control_plane_service, ctx):
    """Delegate session file presence/creation to control-plane service."""
    return control_plane_service.ensure_session_file(ctx)


def read_session_start_time(control_plane_service, ctx):
    """Delegate session start timestamp read to control-plane service."""
    return control_plane_service.read_session_start_time(ctx)


def write_session_start_time(control_plane_service, ctx, timestamp=None):
    """Delegate session start timestamp write to control-plane service."""
    return control_plane_service.write_session_start_time(ctx, timestamp)


def clear_session_start_time(control_plane_service, ctx):
    """Delegate session file clear operation to control-plane service."""
    return control_plane_service.clear_session_start_time(ctx)


def get_session_start_time(control_plane_service, ctx, service_status=None):
    """Delegate logical session-start lookup to control-plane service."""
    return control_plane_service.get_session_start_time(ctx, service_status)


def get_session_duration_text(control_plane_service, ctx):
    """Delegate rendered session duration text to control-plane service."""
    return control_plane_service.get_session_duration_text(ctx)


def ensure_session_tracking_initialized(session_state, initialize_fn):
    """Run session tracking initialization once per process."""
    if session_state.initialized:
        return
    with session_state.init_lock:
        if session_state.initialized:
            return
        initialize_fn()
        session_state.initialized = True
