"""Application bootstrap/run helpers."""


def run_server(app, cfg_get_str, cfg_get_int, log_mcweb_log, log_mcweb_exception, boot_steps):
    """Run startup steps, then start Flask server."""
    host = cfg_get_str("WEB_HOST", "0.0.0.0")
    port = cfg_get_int("WEB_PORT", 8080, minimum=1)
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
        app.run(host=host, port=port)
    except Exception as exc:
        log_mcweb_exception("boot_step/app.run", exc)
        log_mcweb_log("boot-failed", command="app.run", rejection_message=str(exc)[:500] or "web server startup failed")
        raise

