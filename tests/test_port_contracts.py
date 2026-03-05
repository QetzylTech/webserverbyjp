import importlib
import inspect
import unittest


CALLS_MODULES = (
    "app.platform.calls_windows",
    "app.platform.calls_linux_deb",
    "app.platform.calls_mac",
)

METRICS_MODULES = (
    "app.platform.metrics_windows",
    "app.platform.metrics_linux_deb",
    "app.platform.metrics_mac",
)

PATHS_MODULES = (
    "app.platform.paths_windows",
    "app.platform.paths_linux",
    "app.platform.paths_mac",
)


def _assert_module_callable_signature(testcase, module, func_name, required_params):
    target = getattr(module, func_name, None)
    testcase.assertTrue(callable(target), msg=f"{module.__name__}.{func_name} must be callable")
    sig = inspect.signature(target)
    param_names = tuple(sig.parameters.keys())
    for name in required_params:
        testcase.assertIn(name, param_names, msg=f"{module.__name__}.{func_name} missing parameter '{name}'")


class ServiceControlPortContractTests(unittest.TestCase):
    def test_service_control_contract_is_consistent_across_os_modules(self):
        for module_name in CALLS_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                _assert_module_callable_signature(self, module, "default_web_port", ())
                _assert_module_callable_signature(self, module, "service_show_load_state", ("service_name", "timeout", "minecraft_root"))
                _assert_module_callable_signature(self, module, "service_is_active", ("service_name", "timeout", "minecraft_root"))
                _assert_module_callable_signature(self, module, "service_start_no_block", ("service_name", "timeout", "minecraft_root"))
                _assert_module_callable_signature(self, module, "service_start", ("service_name", "timeout", "minecraft_root"))
                _assert_module_callable_signature(self, module, "service_stop", ("service_name", "timeout", "minecraft_root"))
                _assert_module_callable_signature(self, module, "run_elevated", ("cmd", "timeout"))
                _assert_module_callable_signature(self, module, "run_mcrcon", ("host", "port", "password", "command", "timeout"))


class LogPortContractTests(unittest.TestCase):
    def test_log_contract_is_consistent_across_os_modules(self):
        for module_name in CALLS_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                _assert_module_callable_signature(self, module, "minecraft_log_stream_mode", ())
                _assert_module_callable_signature(
                    self,
                    module,
                    "minecraft_load_recent_logs",
                    ("service_name", "logs_dir", "tail_lines", "timeout"),
                )
                _assert_module_callable_signature(
                    self,
                    module,
                    "minecraft_startup_probe_output",
                    ("service_name", "logs_dir", "timeout"),
                )
                _assert_module_callable_signature(self, module, "minecraft_follow_logs_command", ("service_name", "logs_dir"))


class BackupPortContractTests(unittest.TestCase):
    def test_backup_contract_is_consistent_across_os_modules(self):
        for module_name in CALLS_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                _assert_module_callable_signature(self, module, "run_backup_script", ("script_path", "trigger", "timeout"))


class MetricsPortContractTests(unittest.TestCase):
    def test_metrics_contract_is_consistent_across_os_modules(self):
        for module_name in METRICS_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                _assert_module_callable_signature(self, module, "get_cpu_usage_per_core", ())
                _assert_module_callable_signature(self, module, "get_ram_usage", ())
                _assert_module_callable_signature(self, module, "get_cpu_frequency", ())
                _assert_module_callable_signature(self, module, "get_storage_usage", ())


class PathContractTests(unittest.TestCase):
    def test_path_contract_is_consistent_across_os_modules(self):
        for module_name in PATHS_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                _assert_module_callable_signature(self, module, "default_minecraft_root", ("user_name",))
                _assert_module_callable_signature(self, module, "default_backup_dir", ("user_name",))
                _assert_module_callable_signature(self, module, "is_valid_env_path", ("path_text",))


if __name__ == "__main__":
    unittest.main()
