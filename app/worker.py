"""Background worker entrypoint for split-role deployments."""

from app.main import run_worker


def main():
    """Run the MC Web background worker process."""
    run_worker()


if __name__ == "__main__":
    main()
