"""Application bootstrap/run helpers."""
from app.ports import ports


def run_server(app, app_config, log_mcweb_log, log_mcweb_exception, boot_steps):
    """Run startup steps, then start Flask server."""
    host = "0.0.0.0"
    default_port = 8080
    try:
        default_port = int(ports.service_control.default_web_port())
    except Exception:
        default_port = 8080
    try:
        port = int(getattr(app_config, "port", default_port))
    except Exception:
        port = default_port
    log_mcweb_log("boot-start", command=f"host={host} port={port}")

    for step_name, step_func in boot_steps:
        try:
            step_func()
        except Exception as exc:
            log_mcweb_exception(f"boot_step/{step_name}", exc)
            log_mcweb_log("boot-failed", command=step_name, rejection_message=str(exc)[:500] or "startup step failed")
            raise

    log_mcweb_log("boot-ready", command=f"host={host} port={port}")
    try:
        # Enable concurrent request handling so SSE/log streams keep updating
        # while long-running actions (e.g., backups) are in-flight.
        app.run(host=host, port=port, threaded=True)
    except Exception as exc:
        log_mcweb_exception("boot_step/app.run", exc)
        log_mcweb_log("boot-failed", command="app.run", rejection_message=str(exc)[:500] or "web server startup failed")
        raise

