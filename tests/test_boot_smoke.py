import runpy
import unittest
from unittest.mock import patch

from flask import Flask


class WebBootSmokeTests(unittest.TestCase):
    def test_application_factory_create_app_returns_flask_instance(self):
        from app.application_factory import create_app

        app = create_app()
        self.assertIsInstance(app, Flask)

    def test_main_entrypoint_uses_web_boot_path_for_non_worker_role(self):
        import app.main as main_app

        with patch.object(main_app, "PROCESS_ROLE", "web"), patch.object(main_app, "run_server") as run_server_mock, patch.object(
            main_app, "run_worker"
        ) as run_worker_mock:
            main_app.main()
            run_server_mock.assert_called_once_with()
            run_worker_mock.assert_not_called()


class WorkerBootSmokeTests(unittest.TestCase):
    def test_worker_bootstrap_delegates_to_web_runtime_worker(self):
        from app.bootstrap import worker_app

        with patch("app.bootstrap.web_app.run_worker", return_value=None) as run_worker_mock:
            worker_app.run_worker()
            run_worker_mock.assert_called_once_with()

    def test_main_entrypoint_uses_worker_boot_path_for_worker_role(self):
        import app.main as main_app

        with patch.object(main_app, "PROCESS_ROLE", "worker"), patch.object(main_app, "run_server") as run_server_mock, patch.object(
            main_app, "run_worker"
        ) as run_worker_mock:
            main_app.main()
            run_worker_mock.assert_called_once_with()
            run_server_mock.assert_not_called()


class DebugBootSmokeTests(unittest.TestCase):
    def test_debug_application_factory_create_app_returns_flask_instance(self):
        from debug.application_factory import create_app

        app = create_app()
        self.assertIsInstance(app, Flask)

    def test_standalone_debug_launcher_executes_run_server(self):
        with patch("debug.main.run_server", return_value=None) as run_server_mock:
            runpy.run_module("debug_app", run_name="__main__")
            run_server_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
