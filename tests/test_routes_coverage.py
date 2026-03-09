import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from app.routes import dashboard_control_routes as control_routes
from debug import routes as debug_routes
from app.routes import dashboard_file_routes as file_routes
from app.routes import dashboard_maintenance_api_routes as maintenance_routes
from app.routes import dashboard_routes as home_routes
from app.routes import setup_routes


def _assert_rule_methods(testcase, app, rule, methods):
    matching = []
    for entry in app.url_map.iter_rules():
        if entry.rule == rule:
            matching.append(entry)
    testcase.assertTrue(bool(matching), msg=f"Missing route: {rule}")
    combined_methods = set()
    for entry in matching:
        combined_methods.update(entry.methods)
    for method in methods:
        testcase.assertIn(method, combined_methods, msg=f"Route {rule} missing method {method}")


class ControlRoutesCoverageTests(unittest.TestCase):
    def test_control_routes_registered_and_smoke(self):
        app = Flask(__name__)
        backup_state = SimpleNamespace(lock=threading.Lock(), last_error="")
        state = {
                "is_storage_low": lambda: False,
                "low_storage_error_message": lambda: "low",
                "log_mcweb_action": lambda *_args, **_kwargs: None,
                "_low_storage_blocked_response": lambda message: (message, 409),
                "set_service_status_intent": lambda *_args, **_kwargs: None,
                "invalidate_status_cache": lambda: None,
                "write_session_start_time": lambda: 1.0,
                "_session_write_failed_response": lambda: ("session failed", 500),
                "reset_backup_schedule_state": lambda: None,
                "start_service_non_blocking": lambda timeout=12: {"ok": True},
                "log_mcweb_exception": lambda *_args, **_kwargs: None,
                "_start_failed_response": lambda message: (message, 500),
                "_ok_response": lambda: ("ok", 200),
                "validate_sudo_password": lambda password: password == "ok",
                "_password_rejected_response": lambda: ("password incorrect", 403),
                "record_successful_password_ip": lambda: None,
                "graceful_stop_minecraft": lambda: {"systemd_ok": True, "backup_ok": True},
                "clear_session_start_time": lambda: None,
                "run_backup_script": lambda trigger="manual": True,
                "backup_state": backup_state,
                "_backup_failed_response": lambda message: (message, 500),
                "start_restore_job": lambda filename: {"ok": True, "job_id": "j1"},
                "get_restore_status": lambda since_seq="0", job_id=None: {"ok": True, "running": False, "events": [], "result": {"ok": True}},
                "_rcon_rejected_response": lambda message, status=400: (message, status),
                "is_rcon_enabled": lambda: True,
                "get_status": lambda: "active",
                "_run_mcrcon": lambda command, timeout=8: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
                "APP_STATE_DB_PATH": Path("data/test_app_state_routes.sqlite3"),
            }

        control_routes.register_control_routes(app, state, run_cleanup_event_if_enabled=lambda *_args, **_kwargs: None)
        client = app.test_client()

        _assert_rule_methods(self, app, "/start", {"POST"})
        _assert_rule_methods(self, app, "/stop", {"POST"})
        _assert_rule_methods(self, app, "/backup", {"POST"})
        _assert_rule_methods(self, app, "/restore-backup", {"POST"})
        _assert_rule_methods(self, app, "/restore-status", {"GET"})
        _assert_rule_methods(self, app, "/operation-status/<op_id>", {"GET"})
        _assert_rule_methods(self, app, "/rcon", {"POST"})

        start_resp = client.post("/start")
        self.assertEqual(start_resp.status_code, 202)
        start_op_id = (start_resp.get_json() or {}).get("op_id", "")
        self.assertTrue(start_op_id)
        self.assertEqual(client.get(f"/operation-status/{start_op_id}").status_code, 200)

        stop_resp = client.post("/stop", data={"sudo_password": "ok"})
        self.assertEqual(stop_resp.status_code, 202)
        stop_op_id = (stop_resp.get_json() or {}).get("op_id", "")
        self.assertTrue(stop_op_id)
        self.assertEqual(client.get(f"/operation-status/{stop_op_id}").status_code, 200)
        backup_resp = client.post("/backup")
        self.assertEqual(backup_resp.status_code, 202)
        backup_op_id = (backup_resp.get_json() or {}).get("op_id", "")
        self.assertTrue(backup_op_id)
        self.assertEqual(client.get(f"/operation-status/{backup_op_id}").status_code, 200)

        restore_resp = client.post("/restore-backup", data={"sudo_password": "ok", "filename": "a.zip"})
        self.assertEqual(restore_resp.status_code, 202)
        restore_op_id = (restore_resp.get_json() or {}).get("op_id", "")
        self.assertTrue(restore_op_id)
        self.assertEqual(client.get(f"/operation-status/{restore_op_id}").status_code, 200)
        self.assertEqual(client.get("/restore-status?since=0").status_code, 200)
        self.assertEqual(
            client.post("/rcon", data={"sudo_password": "ok", "rcon_command": "list"}).status_code,
            200,
        )


class DebugRoutesCoverageTests(unittest.TestCase):
    def test_debug_routes_registered_and_smoke(self):
        app = Flask(__name__)
        backup_state = SimpleNamespace(lock=threading.Lock(), last_error="")
        state = {
            "DEBUG_PAGE_VISIBLE": True,
            "DEBUG_ENABLED": True,
            "get_debug_server_properties_rows": lambda: {"ok": True, "path": "server.properties"},
            "get_debug_env_rows": lambda: [],
            "_ensure_csrf_token": lambda: "t",
            "validate_sudo_password": lambda password: password == "ok",
            "log_debug_page_action": lambda *_args, **_kwargs: None,
            "record_successful_password_ip": lambda: None,
            "DEBUG_SERVER_PROPERTIES_KEYS": ["motd"],
            "set_debug_server_properties_values": lambda values: {"ok": True, "path": "server.properties"},
            "reset_all_debug_overrides": lambda: None,
            "debug_env_original_values": {"A": "1"},
            "apply_debug_env_overrides": lambda updates: [],
            "debug_explorer_list": lambda root, rel_path: {"ok": True, "items": []},
            "is_storage_low": lambda: False,
            "low_storage_error_message": lambda: "low",
            "debug_start_service": lambda: True,
            "debug_schedule_backup": lambda minutes, trigger="manual": (True, ""),
            "debug_run_backup": lambda trigger="manual": True,
            "backup_state": backup_state,
            "debug_stop_service": lambda password: (True, ""),
        }
        with patch.object(debug_routes, "render_template", return_value="debug-page"):
            debug_routes.register_debug_routes(app, state)
            client = app.test_client()

            _assert_rule_methods(self, app, "/debug", {"GET"})
            _assert_rule_methods(self, app, "/debug/server-properties", {"GET", "POST"})
            _assert_rule_methods(self, app, "/debug/env", {"POST"})
            _assert_rule_methods(self, app, "/debug/explorer/list", {"GET"})
            _assert_rule_methods(self, app, "/debug/start", {"POST"})
            _assert_rule_methods(self, app, "/debug/backup", {"POST"})
            _assert_rule_methods(self, app, "/debug/stop", {"POST"})

            self.assertEqual(client.get("/debug").status_code, 200)
            self.assertEqual(client.get("/debug/server-properties").status_code, 200)
            self.assertEqual(client.post("/debug/server-properties", data={"sudo_password": "ok"}).status_code, 200)
            self.assertEqual(client.post("/debug/env", data={"sudo_password": "ok"}).status_code, 302)
            self.assertEqual(client.get("/debug/explorer/list").status_code, 200)
            self.assertEqual(client.post("/debug/start").status_code, 302)
            self.assertEqual(client.post("/debug/backup", data={"mode": "manual"}).status_code, 302)
            self.assertEqual(client.post("/debug/stop", data={"sudo_password": "ok"}).status_code, 302)


class FileRoutesCoverageTests(unittest.TestCase):
    def test_file_routes_registered_and_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "backups"
            crash_dir = root / "crash"
            mc_logs_dir = root / "mc_logs"
            app_logs_dir = root / "app_logs"
            docs_dir = root / "doc"
            for directory in (backup_dir, crash_dir, mc_logs_dir, app_logs_dir, docs_dir):
                directory.mkdir(parents=True, exist_ok=True)
            (backup_dir / "a.zip").write_text("zip", encoding="utf-8")
            (crash_dir / "crash.txt").write_text("crash", encoding="utf-8")
            (mc_logs_dir / "latest.log").write_text("log", encoding="utf-8")
            (app_logs_dir / "backup.log").write_text("backup", encoding="utf-8")

            app = Flask(__name__)
            state = {
                "MCWEB_LOG_FILE": app_logs_dir / "mcweb.log",
                "MINECRAFT_LOGS_DIR": mc_logs_dir,
                "DISPLAY_TZ": __import__("zoneinfo").ZoneInfo("UTC"),
                "_list_download_files": lambda base_dir, pattern, display_tz: [
                    {
                        "name": path.name,
                        "mtime": path.stat().st_mtime,
                        "size_bytes": path.stat().st_size,
                        "modified": "x",
                        "size_text": "x",
                    }
                    for path in base_dir.glob(pattern)
                    if path.is_file()
                ],
                "_safe_filename_in_dir": lambda base_dir, filename: filename if (base_dir / filename).exists() else None,
                "ensure_file_page_cache_refresher_started": lambda: None,
                "_mark_file_page_client_active": lambda: None,
                "get_cached_file_page_items": lambda key: [],
                "_ensure_csrf_token": lambda: "t",
                "FILE_PAGE_HEARTBEAT_INTERVAL_MS": 1000,
                "validate_sudo_password": lambda password: password == "ok",
                "_password_rejected_response": lambda: ("password incorrect", 403),
                "record_successful_password_ip": lambda: None,
                "BACKUP_DIR": backup_dir,
                "log_mcweb_action": lambda *_args, **_kwargs: None,
                "CRASH_REPORTS_DIR": crash_dir,
                "get_log_source_text": lambda source: "logs",
                "get_cached_dashboard_metrics": lambda: {"ok": True},
                "metrics_cache_cond": threading.Condition(),
                "metrics_stream_client_count": 0,
                "metrics_cache_seq": 0,
                "metrics_cache_payload": {},
                "METRICS_STREAM_HEARTBEAT_SECONDS": 0.1,
            }
            with patch.object(file_routes, "render_template", return_value="files-page"):
                file_routes.register_file_routes(app, state)
                client = app.test_client()

                _assert_rule_methods(self, app, "/backups", {"GET"})
                _assert_rule_methods(self, app, "/crash-logs", {"GET"})
                _assert_rule_methods(self, app, "/minecraft-logs", {"GET"})
                _assert_rule_methods(self, app, "/file-page-heartbeat", {"POST"})
                _assert_rule_methods(self, app, "/download/backups/<path:filename>", {"POST"})
                _assert_rule_methods(self, app, "/download/backups-snapshot/<path:snapshot_name>", {"POST"})
                _assert_rule_methods(self, app, "/download/crash-logs/<path:filename>", {"GET"})
                _assert_rule_methods(self, app, "/download/minecraft-logs/<path:filename>", {"GET"})
                _assert_rule_methods(self, app, "/download/log-files/<source>/<path:filename>", {"GET"})
                _assert_rule_methods(self, app, "/log-files/<source>", {"GET"})
                _assert_rule_methods(self, app, "/view-file/<source>/<path:filename>", {"GET"})
                _assert_rule_methods(self, app, "/view-log-file/<source>/<path:filename>", {"GET"})
                _assert_rule_methods(self, app, "/log-stream/<source>", {"GET"})
                _assert_rule_methods(self, app, "/log-text/<source>", {"GET"})
                _assert_rule_methods(self, app, "/metrics", {"GET"})
                _assert_rule_methods(self, app, "/metrics-stream", {"GET"})

                self.assertEqual(client.get("/backups").status_code, 200)
                crash_redirect = client.get("/crash-logs")
                self.assertEqual(crash_redirect.status_code, 302)
                self.assertIn("/minecraft-logs?source=crash", crash_redirect.headers.get("Location", ""))
                self.assertEqual(client.get("/minecraft-logs").status_code, 200)
                self.assertEqual(client.post("/file-page-heartbeat").status_code, 204)
                self.assertEqual(client.post("/download/backups/a.zip", data={"sudo_password": "ok"}).status_code, 200)
                self.assertEqual(client.post("/download/backups-snapshot/nope", data={"sudo_password": "ok"}).status_code, 404)
                self.assertEqual(client.get("/download/crash-logs/crash.txt").status_code, 200)
                self.assertEqual(client.get("/download/minecraft-logs/latest.log").status_code, 200)
                self.assertEqual(client.get("/download/log-files/backup/backup.log").status_code, 200)
                self.assertEqual(client.get("/download/log-files/crash/crash.txt").status_code, 200)
                self.assertEqual(client.get("/log-files/minecraft").status_code, 200)
                self.assertEqual(client.get("/log-files/crash").status_code, 200)
                self.assertEqual(client.get("/view-file/crash_logs/crash.txt").status_code, 200)
                self.assertEqual(client.get("/view-log-file/backup/backup.log").status_code, 200)
                self.assertEqual(client.get("/view-log-file/crash/crash.txt").status_code, 200)
                self.assertEqual(client.get("/log-text/backup").status_code, 200)
                self.assertEqual(client.get("/metrics").status_code, 200)


class MaintenanceRoutesCoverageTests(unittest.TestCase):
    def test_maintenance_routes_registered_and_smoke(self):
        app = Flask(__name__)
        state = {
            "_ensure_csrf_token": lambda: "t",
            "get_device_name_map": lambda: {"127.0.0.1": "local"},
            "DISPLAY_TZ": "UTC",
            "WORLD_DIR": "/world",
            "BACKUP_DIR": "/backups",
            "validate_sudo_password": lambda password: password == "ok",
            "record_successful_password_ip": lambda: None,
        }

        query_patches = {
            "start_worker": lambda *_args, **_kwargs: None,
            "_cleanup_load_config": lambda _state: {"rules": {"enabled": True}, "meta": {}},
            "_cleanup_normalize_scope": lambda _scope: "backups",
            "_cleanup_get_scope_view": lambda full_cfg, scope: {"rules": {"enabled": True}, "meta": {}},
            "_cleanup_state_snapshot": lambda _state, _cfg: {
                "config": {},
                "non_normal": {},
                "storage": {},
                "history": [],
                "next_run_at": None,
            },
            "_cleanup_evaluate": lambda _state, _cfg, **kwargs: {
                "requested_delete_count": 0,
                "capped_delete_count": 0,
                "selected_ineligible": [],
                "errors": [],
                "deleted_count": 0,
            },
            "_cleanup_active_world_path": lambda _state: Path("/world"),
            "_cleanup_data_dir": lambda _state: Path("/tmp"),
        }
        command_patches = {
            "start_cleanup_scheduler_once": lambda _state: None,
            "_cleanup_validate_rules": lambda rules: (True, rules),
            "_cleanup_apply_scope_from_state": lambda _state, parsed, scope="backups": parsed,
            "_cleanup_now_iso": lambda _state: "2026-03-04T00:00:00Z",
            "_cleanup_get_client_ip": lambda _state: "127.0.0.1",
            "_cleanup_load_config": lambda _state: {"rules": {"enabled": True}, "meta": {}},
            "_cleanup_get_scope_view": lambda full_cfg, scope: {"rules": {"enabled": True}, "meta": {}},
            "_cleanup_save_config": lambda _state, _cfg: None,
            "_cleanup_log": lambda *_args, **_kwargs: None,
            "_cleanup_run_with_lock": lambda _state, _cfg, **kwargs: {
                "deleted_count": 0,
                "errors": [],
                "requested_delete_count": 0,
                "capped_delete_count": 0,
            },
            "_cleanup_evaluate": lambda _state, _cfg, **kwargs: {
                "requested_delete_count": 0,
                "capped_delete_count": 0,
                "selected_ineligible": [],
                "errors": [],
                "deleted_count": 0,
            },
            "_cleanup_append_history": lambda *_args, **_kwargs: None,
            "_cleanup_error": lambda code, message=None, status=400: ({"ok": False, "error": code, "message": message or ""}, status),
            "_cleanup_load_non_normal": lambda _state: {"missed_runs": []},
            "_cleanup_atomic_write_json": lambda path, data: None,
            "_cleanup_non_normal_path": lambda _state: Path("/tmp/non_normal.json"),
            "_cleanup_normalize_scope": lambda _scope: "backups",
        }

        with patch.object(maintenance_routes, "render_template", return_value="maintenance-page"), \
             patch.multiple(maintenance_routes.maintenance_queries_service, **query_patches), \
             patch.multiple(maintenance_routes.maintenance_commands_service, **command_patches):
            maintenance_routes.register_maintenance_routes(app, state)
            client = app.test_client()

            _assert_rule_methods(self, app, "/maintenance", {"GET"})
            _assert_rule_methods(self, app, "/maintenance/api/state", {"GET"})
            _assert_rule_methods(self, app, "/maintenance/api/confirm-password", {"POST"})
            _assert_rule_methods(self, app, "/maintenance/api/save-rules", {"POST"})
            _assert_rule_methods(self, app, "/maintenance/api/run-rules", {"POST"})
            _assert_rule_methods(self, app, "/maintenance/api/manual-delete", {"POST"})
            _assert_rule_methods(self, app, "/maintenance/api/ack-non-normal", {"POST"})

            self.assertEqual(client.get("/maintenance").status_code, 200)
            state_resp = client.get("/maintenance/api/state")
            self.assertEqual(state_resp.status_code, 200)
            state_body = state_resp.get_json() or {}
            self.assertIn("freshness", state_body)
            self.assertIn("computed_at_epoch", state_body.get("freshness", {}))
            self.assertEqual(
                client.post("/maintenance/api/confirm-password", json={"action": "open_rules_edit", "sudo_password": "ok"}).status_code,
                200,
            )
            self.assertEqual(client.post("/maintenance/api/save-rules", json={"sudo_password": "ok", "rules": {}}).status_code, 200)
            self.assertEqual(client.post("/maintenance/api/run-rules", json={"dry_run": True}).status_code, 200)
            self.assertEqual(client.post("/maintenance/api/manual-delete", json={"dry_run": True, "selected_paths": []}).status_code, 200)
            self.assertEqual(client.post("/maintenance/api/ack-non-normal", json={}).status_code, 200)

    def test_maintenance_api_state_uses_short_ttl_cache(self):
        app = Flask(__name__)
        state = {
            "_ensure_csrf_token": lambda: "t",
            "get_device_name_map": lambda: {"127.0.0.1": "local"},
            "DISPLAY_TZ": "UTC",
            "WORLD_DIR": "/world",
            "BACKUP_DIR": "/backups",
            "validate_sudo_password": lambda password: password == "ok",
            "record_successful_password_ip": lambda: None,
        }
        calls = {"load_cfg": 0, "evaluate": 0, "snapshot": 0}

        def _load_cfg(_state):
            calls["load_cfg"] += 1
            return {"rules": {"enabled": True}, "meta": {}}

        def _eval(_state, _cfg, **_kwargs):
            calls["evaluate"] += 1
            return {"items": [], "requested_delete_count": 0, "capped_delete_count": 0, "selected_ineligible": [], "errors": [], "deleted_count": 0}

        def _snap(_state, _cfg):
            calls["snapshot"] += 1
            return {"config": {}, "non_normal": {}, "storage": {}, "history": [], "next_run_at": None}

        query_patches = {
            "start_worker": lambda *_args, **_kwargs: None,
            "_cleanup_load_config": _load_cfg,
            "_cleanup_normalize_scope": lambda _scope: "backups",
            "_cleanup_get_scope_view": lambda full_cfg, scope: {"rules": {"enabled": True}, "meta": {}},
            "_cleanup_state_snapshot": _snap,
            "_cleanup_evaluate": _eval,
            "_cleanup_active_world_path": lambda _state: Path("/world"),
            "_cleanup_data_dir": lambda _state: Path("/tmp"),
        }
        command_patches = {
            "start_cleanup_scheduler_once": lambda _state: None,
            "_cleanup_normalize_scope": lambda _scope: "backups",
        }

        with patch.object(maintenance_routes, "render_template", return_value="maintenance-page"), \
             patch.multiple(maintenance_routes.maintenance_queries_service, **query_patches), \
             patch.multiple(maintenance_routes.maintenance_commands_service, **command_patches):
            maintenance_routes.register_maintenance_routes(app, state)
            client = app.test_client()
            first = client.get("/maintenance/api/state")
            second = client.get("/maintenance/api/state")
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(calls["load_cfg"], 1)
            self.assertEqual(calls["evaluate"], 1)
            self.assertEqual(calls["snapshot"], 1)


class HomeRoutesCoverageTests(unittest.TestCase):
    def test_home_routes_registered_and_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs_dir = root / "doc"
            docs_dir.mkdir(parents=True)
            (docs_dir / "server_setup_doc.md").write_text("# Doc", encoding="utf-8")

            app = Flask(__name__)
            state = {
                "low_storage_error_message": lambda: "low",
                "_mark_home_page_client_active": lambda: None,
                "get_cached_dashboard_metrics": lambda: {
                    "service_status": "Off",
                    "service_status_class": "stat-red",
                    "service_running_status": "inactive",
                    "backups_status": "ready",
                    "cpu_per_core_items": [],
                    "cpu_frequency": "n/a",
                    "cpu_frequency_class": "stat-red",
                    "storage_usage": "n/a",
                    "storage_usage_class": "stat-red",
                    "players_online": "0",
                    "tick_rate": "0",
                    "session_duration": "--",
                    "idle_countdown": "--",
                    "backup_status": "Idle",
                    "backup_status_class": "stat-yellow",
                    "last_backup_time": "--",
                    "next_backup_time": "--",
                    "server_time": "--",
                    "world_name": "world",
                    "ram_usage": "n/a",
                    "ram_usage_class": "stat-red",
                    "rcon_enabled": True,
                },
                "is_storage_low": lambda: False,
                "get_log_source_text": lambda source: "",
                "_ensure_csrf_token": lambda: "t",
                "HOME_PAGE_HEARTBEAT_INTERVAL_MS": 1000,
                "log_mcweb_log": lambda *_args, **_kwargs: None,
                "FAVICON_URL": "https://example.com/favicon.ico",
                "DOCS_DIR": docs_dir,
                "DOC_README_URL": "/doc/server_setup_doc.md",
                "get_device_name_map": lambda: {"127.0.0.1": "local"},
                "_get_client_ip": lambda: "127.0.0.1",
                "validate_sudo_password": lambda password: password == "ok",
                "_password_rejected_response": lambda: ("password incorrect", 403),
                "record_successful_password_ip": lambda: None,
                "get_observed_state": lambda: {
                    "service_status_display": "Off",
                },
                "get_consistency_report": lambda auto_repair=False: {
                    "ok": True,
                    "issues": [],
                    "repairs": [],
                },
            }
            with patch.object(home_routes, "render_template", return_value="home-page"), \
                 patch.object(home_routes, "register_file_routes", lambda app, state, get_nav_alert_state_from_request=None: None), \
                 patch.object(home_routes, "register_maintenance_routes", lambda app, state: None), \
                 patch.object(home_routes, "register_control_routes", lambda app, state, run_cleanup_event_if_enabled: None):
                home_routes.register_routes(app, state)
                client = app.test_client()

                _assert_rule_methods(self, app, "/", {"GET"})
                _assert_rule_methods(self, app, "/home-heartbeat", {"POST"})
                _assert_rule_methods(self, app, "/ui-error-log", {"POST"})
                _assert_rule_methods(self, app, "/favicon.ico", {"GET"})
                _assert_rule_methods(self, app, "/readme", {"GET"})
                _assert_rule_methods(self, app, "/doc/server_setup_doc.md", {"GET"})
                _assert_rule_methods(self, app, "/doc/readme-url", {"GET"})
                _assert_rule_methods(self, app, "/observed-state", {"GET"})
                _assert_rule_methods(self, app, "/consistency-check", {"GET"})
                _assert_rule_methods(self, app, "/device-name-map", {"GET"})
                _assert_rule_methods(self, app, "/maintenance/nav-alert/restore-pane-open", {"POST"})
                _assert_rule_methods(self, app, "/maintenance/nav-alert/state", {"GET"})

                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.post("/home-heartbeat").status_code, 204)
                self.assertEqual(client.post("/ui-error-log", json={"error_code": "x", "action": "a", "message": "m"}).status_code, 204)
                self.assertEqual(client.get("/favicon.ico").status_code, 302)
                self.assertEqual(client.get("/readme").status_code, 200)
                self.assertEqual(client.get("/doc/server_setup_doc.md").status_code, 200)
                self.assertEqual(client.get("/doc/readme-url").status_code, 200)
                self.assertEqual(client.get("/observed-state").status_code, 200)
                self.assertEqual(client.get("/consistency-check").status_code, 200)
                self.assertEqual(client.get("/device-name-map").status_code, 200)
                self.assertEqual(client.post("/maintenance/nav-alert/restore-pane-open", json={"filename": "f.zip"}).status_code, 204)
                self.assertEqual(client.get("/maintenance/nav-alert/state").status_code, 200)


class SetupRoutesCoverageTests(unittest.TestCase):
    def test_setup_routes_registered_and_smoke(self):
        app = Flask(__name__)
        defaults = {
            "SERVICE": "minecraft",
            "DISPLAY_TZ": "UTC",
            "MINECRAFT_ROOT_DIR": "/mc",
            "BACKUP_DIR": "/backups",
            "MCWEB_ADMIN_PASSWORD_HASH": "h",
            "MCWEB_SECRET_KEY": "k",
        }

        with patch.object(setup_routes, "render_template", return_value="setup-page"), \
             patch.object(setup_routes.setup_service_service, "validate_service_name", return_value=""), \
             patch.object(setup_routes.setup_service_service, "validate_minecraft_root", return_value={"errors": []}), \
             patch.object(setup_routes.setup_service_service, "validate_backup_location", return_value={"errors": [], "missing": False}):
            setup_routes.register_setup_routes(
                app,
                is_setup_required=lambda: True,
                setup_mode=lambda: "full",
                setup_defaults=lambda: dict(defaults),
                save_setup_values=lambda values: (True, "", {}),
            )
            client = app.test_client()

            _assert_rule_methods(self, app, "/setup", {"GET"})
            _assert_rule_methods(self, app, "/setup/validate", {"POST"})
            _assert_rule_methods(self, app, "/setup/submit", {"POST"})

            self.assertEqual(client.get("/setup").status_code, 200)
            self.assertEqual(
                client.post("/setup/validate", json={"kind": "timezone", "values": {"DISPLAY_TZ": "UTC"}}).status_code,
                200,
            )
            self.assertEqual(
                client.post("/setup/validate", json={"kind": "root", "values": {"MINECRAFT_ROOT_DIR": "/mc"}}).status_code,
                200,
            )
            self.assertEqual(
                client.post("/setup/validate", json={"kind": "backup", "values": {"BACKUP_DIR": "/backups"}}).status_code,
                200,
            )
            self.assertEqual(
                client.post(
                    "/setup/submit",
                    data={
                        "service": "minecraft",
                        "display_tz": "UTC",
                        "minecraft_root_dir": "/mc",
                        "backup_dir": "/backups",
                        "admin_password": "password123",
                        "admin_password_confirm": "password123",
                    },
                ).status_code,
                200,
            )


if __name__ == "__main__":
    unittest.main()


