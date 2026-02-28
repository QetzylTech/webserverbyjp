"""Thin launcher for the Minecraft web dashboard."""

from app.application_factory import create_app
from app.main import run_server

app = create_app()


if __name__ == "__main__":
    run_server()
