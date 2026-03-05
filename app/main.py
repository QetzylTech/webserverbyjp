"""Thin launcher facade for composed web/worker apps."""

from app.bootstrap.web_app import *  # noqa: F401,F403
from app.bootstrap.web_app import PROCESS_ROLE, run_server
from app.bootstrap.worker_app import run_worker


def main():
    """Run either worker or web role based on process role."""
    if PROCESS_ROLE == "worker":
        run_worker()
    else:
        run_server()


if __name__ == "__main__":
    main()
