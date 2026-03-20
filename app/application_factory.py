"""App factory and runtime wiring entrypoint."""

from flask import Flask


def create_app() -> Flask:
    """Return the Flask app instance used by WSGI/ASGI entrypoints."""
    from app.bootstrap.web_app import app

    return app
