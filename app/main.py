"""Launch the configured web or worker process entrypoint."""

from app.bootstrap.web_app import PROCESS_ROLE, run_server
from app.bootstrap.worker_app import run_worker

__all__ = ['PROCESS_ROLE', 'run_server', 'run_worker', 'main']


def main():
    """Run either worker or web role based on process role."""
    if PROCESS_ROLE == 'worker':
        run_worker()
    else:
        run_server()


if __name__ == "__main__":
    main()

