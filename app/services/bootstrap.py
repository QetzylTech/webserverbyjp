"""Application bootstrap/run helpers."""
import platform


def run_server(app, cfg_get_str, cfg_get_int, log_mcweb_log, log_mcweb_exception, boot_steps):
    """Run startup steps, then start Flask server."""
    host = "0.0.0.0"
    system_name = (platform.system() or "").strip().lower()
    port = 80 if system_name == "windows" else 8080
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

