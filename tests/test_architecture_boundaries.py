import ast
import unittest
from pathlib import Path


class ArchitectureBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.app_root = cls.repo_root / "app"

    def _python_files(self, subdir):
        root = self.app_root / subdir
        if not root.exists():
            return []
        return sorted(p for p in root.rglob("*.py") if p.is_file())

    def _is_under_any_root(self, file_path, roots):
        return any(root in file_path.parents for root in roots)

    def _imports_for_file(self, file_path):
        text = file_path.read_text(encoding="utf-8-sig", errors="ignore")
        tree = ast.parse(text, filename=str(file_path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level and (node.module is None):
                    continue
                if node.module:
                    imports.append(node.module)
        return imports

    def _collect_violations(self, files, forbidden_prefixes):
        violations = []
        for file_path in files:
            imports = self._imports_for_file(file_path)
            for imp in imports:
                if any(imp == prefix or imp.startswith(prefix + ".") for prefix in forbidden_prefixes):
                    rel = file_path.relative_to(self.repo_root)
                    violations.append(f"{rel}: {imp}")
        return violations

    def test_core_does_not_depend_on_services_routes_platform_or_main(self):
        files = self._python_files("core")
        forbidden = ("app.services", "app.routes", "app.platform", "app.main")
        violations = self._collect_violations(files, forbidden)
        self.assertEqual([], violations, msg="Core layer dependency violations:\n" + "\n".join(violations))

    def test_routes_do_not_depend_on_platform(self):
        files = self._python_files("routes")
        violations = self._collect_violations(files, ("app.platform",))
        self.assertEqual([], violations, msg="Route layer dependency violations:\n" + "\n".join(violations))

    def test_platform_imports_are_limited_to_platform_and_infrastructure(self):
        allowed_roots = {
            self.app_root / "platform",
            self.app_root / "infrastructure",
        }
        violations = []
        for file_path in sorted(self.app_root.rglob("*.py")):
            if not file_path.is_file():
                continue
            if self._is_under_any_root(file_path, allowed_roots):
                continue
            imports = self._imports_for_file(file_path)
            for imp in imports:
                if imp == "app.platform" or imp.startswith("app.platform."):
                    rel = file_path.relative_to(self.repo_root)
                    violations.append(f"{rel}: {imp}")
        self.assertEqual(
            [],
            violations,
            msg="Platform module imports are only allowed in adapters:\n" + "\n".join(violations),
        )

    def test_services_do_not_depend_on_routes(self):
        files = self._python_files("services")
        violations = self._collect_violations(files, ("app.routes",))
        self.assertEqual([], violations, msg="Service layer dependency violations:\n" + "\n".join(violations))

    def test_services_do_not_accept_generic_state_parameter(self):
        violations = []
        for file_path in self._python_files("services"):
            text = file_path.read_text(encoding="utf-8-sig", errors="ignore")
            tree = ast.parse(text, filename=str(file_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    arg_names = [arg.arg for arg in node.args.args]
                    kwonly_names = [arg.arg for arg in node.args.kwonlyargs]
                    all_names = set(arg_names + kwonly_names)
                    if "state" in all_names:
                        rel = file_path.relative_to(self.repo_root)
                        violations.append(f"{rel}:{node.lineno} -> {node.name}(state)")
        self.assertEqual(
            [],
            violations,
            msg="Service functions must use typed/narrow deps, not generic state:\n" + "\n".join(violations),
        )

    def test_web_config_loading_is_bootstrap_only(self):
        allowed = {
            Path("app/bootstrap/config_loader.py"),
            Path("app/core/web_config.py"),
        }
        violations = []
        for file_path in sorted(self.app_root.rglob("*.py")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(self.repo_root)
            if rel in allowed:
                continue
            imports = self._imports_for_file(file_path)
            for imp in imports:
                if imp == "app.core.web_config" or imp.startswith("app.core.web_config."):
                    violations.append(f"{rel}: {imp}")
        self.assertEqual([], violations, msg="WebConfig imports outside bootstrap:\n" + "\n".join(violations))

    def test_raw_web_cfg_values_only_used_in_bootstrap_or_debug(self):
        allowed_roots = {
            self.app_root / "bootstrap",
            self.repo_root / "debug",
        }
        violations = []
        for file_path in sorted((self.repo_root).rglob("*.py")):
            if not file_path.is_file():
                continue
            in_app = self.app_root in file_path.parents
            in_debug = (self.repo_root / "debug") in file_path.parents
            if not (in_app or in_debug):
                continue
            if self._is_under_any_root(file_path, allowed_roots):
                continue
            text = file_path.read_text(encoding="utf-8-sig", errors="ignore")
            if "_WEB_CFG_VALUES" in text:
                rel = file_path.relative_to(self.repo_root)
                violations.append(f"{rel}: _WEB_CFG_VALUES")
        self.assertEqual(
            [],
            violations,
            msg="Raw web config values should stay in bootstrap/debug only:\n" + "\n".join(violations),
        )

    def test_platform_does_not_depend_on_routes_services_or_main(self):
        files = self._python_files("platform")
        violations = self._collect_violations(files, ("app.routes", "app.services", "app.main"))
        self.assertEqual([], violations, msg="Platform layer dependency violations:\n" + "\n".join(violations))

    def test_commands_do_not_depend_on_platform(self):
        files = self._python_files("commands")
        violations = self._collect_violations(files, ("app.platform",))
        self.assertEqual([], violations, msg="Command layer dependency violations:\n" + "\n".join(violations))

    def test_queries_do_not_depend_on_platform_or_routes(self):
        files = self._python_files("queries")
        violations = self._collect_violations(files, ("app.platform", "app.routes"))
        self.assertEqual([], violations, msg="Query layer dependency violations:\n" + "\n".join(violations))

    def test_commands_do_not_depend_on_routes(self):
        files = self._python_files("commands")
        violations = self._collect_violations(files, ("app.routes",))
        self.assertEqual([], violations, msg="Command layer dependency violations:\n" + "\n".join(violations))

    def test_app_main_is_only_imported_by_entrypoints(self):
        allowed = {
            Path("app/application_factory.py"),
            Path("app/worker.py"),
        }
        violations = []
        for file_path in sorted(self.app_root.rglob("*.py")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(self.repo_root)
            if rel in allowed or rel == Path("app/main.py"):
                continue
            imports = self._imports_for_file(file_path)
            for imp in imports:
                if imp == "app.main" or imp.startswith("app.main."):
                    violations.append(f"{rel}: {imp}")
        self.assertEqual([], violations, msg="Invalid imports of app.main:\n" + "\n".join(violations))

    def test_app_main_imports_only_bootstrap_modules(self):
        main_file = self.app_root / "main.py"
        imports = self._imports_for_file(main_file)
        allowed_prefixes = (
            "app.bootstrap.web_app",
            "app.bootstrap.worker_app",
        )
        violations = []
        for imp in imports:
            if imp.startswith("app.") and not any(imp == p or imp.startswith(p + ".") for p in allowed_prefixes):
                violations.append(f"app/main.py: {imp}")
        self.assertEqual([], violations, msg="app/main.py must stay composition-only:\n" + "\n".join(violations))

    def test_subprocess_only_used_in_platform_or_infrastructure(self):
        allowed_roots = {
            self.app_root / "platform",
            self.app_root / "infrastructure",
        }
        violations = []
        for file_path in sorted(self.app_root.rglob("*.py")):
            if not file_path.is_file():
                continue
            if self._is_under_any_root(file_path, allowed_roots):
                continue
            text = file_path.read_text(encoding="utf-8-sig", errors="ignore")
            tree = ast.parse(text, filename=str(file_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "subprocess":
                            rel = file_path.relative_to(self.repo_root)
                            violations.append(f"{rel}: import subprocess")
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "subprocess":
                        rel = file_path.relative_to(self.repo_root)
                        violations.append(f"{rel}: from subprocess import ...")
            if "subprocess." in text:
                rel = file_path.relative_to(self.repo_root)
                violations.append(f"{rel}: subprocess usage")
        self.assertEqual([], violations, msg="Direct subprocess usage outside adapters:\n" + "\n".join(sorted(set(violations))))

    def test_os_specific_path_primitives_only_in_platform(self):
        allowed_roots = {
            self.app_root / "platform",
            self.app_root / "infrastructure",
        }
        banned_tokens = ("PureWindowsPath", "PurePosixPath")
        violations = []
        for file_path in sorted(self.app_root.rglob("*.py")):
            if not file_path.is_file():
                continue
            if self._is_under_any_root(file_path, allowed_roots):
                continue
            text = file_path.read_text(encoding="utf-8-sig", errors="ignore")
            for token in banned_tokens:
                if token in text:
                    rel = file_path.relative_to(self.repo_root)
                    violations.append(f"{rel}: {token}")
        self.assertEqual([], violations, msg="OS-specific path primitives outside adapters:\n" + "\n".join(violations))

    def test_os_detection_patterns_only_in_platform(self):
        allowed_roots = {
            self.app_root / "platform",
            self.app_root / "infrastructure",
        }
        banned_tokens = (
            "sys.platform",
            "os.name",
            "platform.system(",
            "platform.machine(",
            "os.uname(",
        )
        violations = []
        for file_path in sorted(self.app_root.rglob("*.py")):
            if not file_path.is_file():
                continue
            if self._is_under_any_root(file_path, allowed_roots):
                continue
            text = file_path.read_text(encoding="utf-8-sig", errors="ignore")
            for token in banned_tokens:
                if token in text:
                    rel = file_path.relative_to(self.repo_root)
                    violations.append(f"{rel}: {token}")
        self.assertEqual([], violations, msg="OS-detection outside adapters:\n" + "\n".join(violations))

    def test_filesystem_side_effect_modules_only_in_adapters(self):
        allowed_roots = {
            self.app_root / "platform",
            self.app_root / "infrastructure",
        }
        violations = []
        for file_path in sorted(self.app_root.rglob("*.py")):
            if not file_path.is_file():
                continue
            if self._is_under_any_root(file_path, allowed_roots):
                continue
            text = file_path.read_text(encoding="utf-8-sig", errors="ignore")
            tree = ast.parse(text, filename=str(file_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"shutil", "tempfile"}:
                            rel = file_path.relative_to(self.repo_root)
                            violations.append(f"{rel}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module in {"shutil", "tempfile"}:
                        rel = file_path.relative_to(self.repo_root)
                        violations.append(f"{rel}: from {node.module} import ...")
        self.assertEqual(
            [],
            violations,
            msg="Filesystem side-effect modules outside adapters:\n" + "\n".join(violations),
        )

    def test_platform_loader_calls_only_in_platform_or_infrastructure(self):
        allowed_roots = {
            self.app_root / "platform",
            self.app_root / "infrastructure",
        }
        banned_tokens = ("get_calls(", "get_paths(", "get_metrics(")
        violations = []
        for file_path in sorted(self.app_root.rglob("*.py")):
            if not file_path.is_file():
                continue
            if self._is_under_any_root(file_path, allowed_roots):
                continue
            text = file_path.read_text(encoding="utf-8-sig", errors="ignore")
            for token in banned_tokens:
                if token in text:
                    rel = file_path.relative_to(self.repo_root)
                    violations.append(f"{rel}: {token}")
        self.assertEqual(
            [],
            violations,
            msg="Direct platform loader calls outside adapters:\n" + "\n".join(violations),
        )

    def test_background_threads_are_started_only_via_worker_scheduler(self):
        allowed = {
            self.app_root / "services" / "worker_scheduler.py",
        }
        violations = []
        for file_path in sorted(self.app_root.rglob("*.py")):
            if not file_path.is_file():
                continue
            if file_path in allowed:
                continue
            text = file_path.read_text(encoding="utf-8-sig", errors="ignore")
            if ".Thread(" in text:
                rel = file_path.relative_to(self.repo_root)
                violations.append(f"{rel}: .Thread(")
        self.assertEqual(
            [],
            violations,
            msg="Direct thread construction outside worker scheduler:\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
