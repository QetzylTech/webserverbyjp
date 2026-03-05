"""App factory and runtime wiring entrypoint."""
def create_app():
    """Return the Flask app instance used by WSGI/ASGI entrypoints."""
    from app.bootstrap.web_app import app

    return app
