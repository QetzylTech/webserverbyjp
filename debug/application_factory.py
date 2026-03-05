"""Debug app factory entrypoint."""


def create_app():
    """Return the standalone debug Flask app."""
    from debug.main import app

    return app
