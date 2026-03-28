import unittest
from unittest.mock import Mock

from flask import Flask

from app.services import app_lifecycle


class AppLifecycleTests(unittest.TestCase):
    def test_install_flask_hooks_only_handles_request_state_and_csrf(self):
        app = Flask(__name__)
        ensure_session_tracking_initialized = Mock()
        ensure_csrf_token = Mock()
        is_csrf_valid = Mock(return_value=True)

        @app.route("/")
        def index():
            return "ok"

        app_lifecycle.install_flask_hooks(
            app,
            ensure_session_tracking_initialized=ensure_session_tracking_initialized,
            ensure_csrf_token=ensure_csrf_token,
            is_csrf_valid=is_csrf_valid,
            csrf_rejected_response=lambda: ("csrf", 403),
            log_mcweb_action=Mock(),
            log_mcweb_exception=Mock(),
        )

        response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        ensure_session_tracking_initialized.assert_called_once_with()
        ensure_csrf_token.assert_called_once_with()
        is_csrf_valid.assert_not_called()

    def test_build_run_server_uses_worker_runtime_boot_step(self):
        bootstrap_service = Mock()
        app = Flask(__name__)
        start_worker_loops = Mock()

        runner = app_lifecycle.build_run_server(
            bootstrap_service=bootstrap_service,
            app=app,
            app_config=object(),
            log_mcweb_log=Mock(),
            log_mcweb_exception=Mock(),
            is_backup_running=lambda: False,
            load_backup_log_cache_from_disk=Mock(),
            load_minecraft_log_cache_from_journal=Mock(),
            load_mcweb_log_cache_from_disk=Mock(),
            ensure_session_tracking_initialized=Mock(),
            warm_file_page_caches=Mock(),
            collect_and_publish_metrics=Mock(),
            start_worker_loops=start_worker_loops,
            enable_background_workers=True,
            enable_boot_runtime_tasks=True,
        )

        runner()

        boot_steps = bootstrap_service.run_server.call_args.args[4]
        step_names = [name for name, _func in boot_steps]
        self.assertIn("start_worker_loops", step_names)
        self.assertNotIn("start_operation_reconciler", step_names)


if __name__ == "__main__":
    unittest.main()
