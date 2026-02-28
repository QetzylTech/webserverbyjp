"""Logging setup helpers."""

from app.core.action_logging import make_log_action, make_log_exception


def build_loggers(display_tz, log_dir, action_log_file, system_log_file, debug_log_file):
    """Create mcweb action/system/debug log writers and exception logger."""
    log_mcweb_action = make_log_action(display_tz, log_dir, action_log_file)
    log_mcweb_log = make_log_action(display_tz, log_dir, system_log_file)
    log_mcweb_exception = make_log_exception(log_mcweb_log)
    log_debug_page_action = make_log_action(display_tz, log_dir, debug_log_file)
    return log_mcweb_action, log_mcweb_log, log_mcweb_exception, log_debug_page_action

