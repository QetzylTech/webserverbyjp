import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from werkzeug.security import generate_password_hash

from app.core import state_store as state_store_service
from app.routes import panel_settings_routes


class PanelSettingsRouteHelpersTests(unittest.TestCase):
    def test_build_device_machine_rows_groups_addresses_and_uses_latest_seen(self):
        fallmap_rows = [
            {"ip": "100.64.0.1", "device_name": "Alice-Laptop", "owner": "Alice"},
            {"ip": "fd7a:115c:a1e0::1", "device_name": "Alice-Laptop", "owner": "Alice"},
            {"ip": "100.64.0.2", "device_name": "Bob-PC", "owner": ""},
        ]
        user_rows = [
            {
                "ip": "100.64.0.1",
                "timestamp": "2026-03-24 08:00:00 PST",
                "device_name": "Alice-Laptop",
                "updated_at": "2026-03-24 16:00:00",
            },
            {
                "ip": "fd7a:115c:a1e0::1",
                "timestamp": "2026-03-25 09:15:00 PST",
                "device_name": "Alice-Laptop",
                "updated_at": "2026-03-25 17:15:00",
            },
        ]

        rows = panel_settings_routes._build_device_machine_rows(fallmap_rows, user_rows)

        self.assertEqual(
            rows,
            [
                {
                    "machine_name": "Alice-Laptop",
                    "addresses": ["100.64.0.1", "fd7a:115c:a1e0::1"],
                    "last_seen": "2026-03-25 09:15:00 PST",
                    "owner": "Alice",
                },
                {
                    "machine_name": "Bob-PC",
                    "addresses": ["100.64.0.2"],
                    "last_seen": "-",
                    "owner": "-",
                },
            ],
        )

    def test_merge_device_maps_preserves_existing_owner_on_import(self):
        existing = [
            {"ip": "100.64.0.1", "device_name": "Alice-Laptop", "owner": "Alice"},
        ]

        merged, conflicts = panel_settings_routes._merge_device_maps(
            existing,
            {"100.64.0.1": "Alice-Workstation", "100.64.0.2": "Bob-PC"},
            mode="append",
            resolution="overwrite",
        )

        self.assertEqual(conflicts, [{"ip": "100.64.0.1", "existing": "Alice-Laptop", "incoming": "Alice-Workstation"}])
        self.assertEqual(
            merged,
            [
                {"ip": "100.64.0.1", "device_name": "Alice-Workstation", "owner": "Alice"},
                {"ip": "100.64.0.2", "device_name": "Bob-PC", "owner": ""},
            ],
        )

    def test_load_user_records_returns_recent_rows(self):
        tmpdir = tempfile.mkdtemp()
        db_path = Path(tmpdir) / "state.sqlite3"
        state_store_service.initialize_state_db(db_path=db_path)
        state_store_service.upsert_user_record(
            db_path,
            ip="100.64.0.9",
            timestamp="2026-03-25 10:00:00 PST",
            device_name="Office-PC",
        )

        rows = state_store_service.load_user_records(db_path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ip"], "100.64.0.9")
        self.assertEqual(rows[0]["timestamp"], "2026-03-25 10:00:00 PST")
        self.assertEqual(rows[0]["device_name"], "Office-PC")
        self.assertTrue(rows[0]["updated_at"])

    def test_validate_superadmin_password_delegates_to_state_binding(self):
        state = {"validate_superadmin_password": Mock(return_value=True)}

        self.assertTrue(panel_settings_routes._validate_superadmin_password(state, "secret"))
        state["validate_superadmin_password"].assert_called_once_with("secret")

    def test_load_env_defaults_prefers_persisted_file_over_stale_runtime_values(self):
        tmpdir = Path(tempfile.mkdtemp())
        docs_dir = tmpdir / "doc"
        docs_dir.mkdir(exist_ok=True)
        web_conf = tmpdir / "mcweb.env"
        web_conf.write_text(
            """# mcweb runtime config

BACKUP_DIR=C:/persisted/backups
DISPLAY_TZ=UTC
MCWEB_ADMIN_PASSWORD_HASH=persisted-admin
MCWEB_REQUIRE_PASSWORD=true
MCWEB_SECRET_KEY=persisted-secret
MCWEB_SUPERADMIN_PASSWORD_HASH=persisted-super
MINECRAFT_ROOT_DIR=C:/persisted/root
SERVICE=minecraft
""",
            encoding="utf-8",
        )
        state = {
            "DOCS_DIR": docs_dir,
            "WEB_CFG_VALUES": {
                "BACKUP_DIR": "C:/stale/backups",
                "DISPLAY_TZ": "Asia/Manila",
                "MCWEB_ADMIN_PASSWORD_HASH": "stale-admin",
                "MCWEB_SUPERADMIN_PASSWORD_HASH": "stale-super",
                "MCWEB_SECRET_KEY": "stale-secret",
                "MINECRAFT_ROOT_DIR": "C:/stale/root",
                "SERVICE": "minecraft",
            },
        }

        defaults, web_conf_path, app_dir = panel_settings_routes._load_env_defaults(state)

        self.assertEqual(app_dir, tmpdir)
        self.assertEqual(web_conf_path, web_conf)
        self.assertEqual(defaults["DISPLAY_TZ"], "UTC")
        self.assertEqual(defaults["MINECRAFT_ROOT_DIR"], "C:/persisted/root")
        self.assertEqual(defaults["BACKUP_DIR"], "C:/persisted/backups")
        self.assertEqual(defaults["MCWEB_SUPERADMIN_PASSWORD_HASH"], "persisted-super")


class PanelSettingsRoutePasswordTests(unittest.TestCase):
    def test_confirm_password_uses_shared_superadmin_throttle(self):
        app = __import__("flask").Flask(__name__)
        state = {
            "validate_superadmin_password": lambda password: start_validator(password),
            "_ensure_csrf_token": lambda: "t",
            "DOCS_DIR": Path("."),
            "WEB_CFG_VALUES": {
                "MCWEB_ADMIN_PASSWORD_HASH": generate_password_hash("admin-pass"),
                "MCWEB_SUPERADMIN_PASSWORD_HASH": generate_password_hash("super-pass"),
            },
            "record_successful_password_ip": lambda: None,
            "APP_STATE_DB_PATH": Path("state.sqlite3"),
            "get_device_name_map": lambda: {},
        }
        ctx = SimpleNamespace(
            ADMIN_PASSWORD_HASH=generate_password_hash("admin-pass"),
            SUPERADMIN_PASSWORD_HASH=generate_password_hash("super-pass"),
            REQUIRE_SUDO_PASSWORD=True,
            password_throttle_lock=threading.Lock(),
            password_throttle_state={"by_ip": {}},
            _get_client_ip=lambda: "100.64.0.9",
            log_mcweb_action=Mock(),
        )

        from app.services import start_usecase

        def start_validator(password):
            return start_usecase.validate_superadmin_password(ctx, password)

        panel_settings_routes.register_panel_settings_routes(app, state)
        client = app.test_client()

        with patch.object(start_usecase._notification_service, "publish_ui_notification") as publish_ui_notification:
            self.assertEqual(
                client.post("/panel-settings/confirm-password", json={"sudo_password": "wrong-1"}).status_code,
                403,
            )
            self.assertEqual(
                client.post("/panel-settings/confirm-password", json={"sudo_password": "wrong-2"}).status_code,
                403,
            )
            self.assertEqual(
                client.post("/panel-settings/confirm-password", json={"sudo_password": "wrong-3"}).status_code,
                403,
            )

        throttle_entry = ctx.password_throttle_state["by_ip"]["100.64.0.9"]
        self.assertGreater(float(throttle_entry["blocked_until"]), 0.0)
        publish_ui_notification.assert_called_once()

    def test_paths_save_persists_values_for_reload(self):
        app = __import__("flask").Flask(__name__)
        tmpdir = Path(tempfile.mkdtemp())
        docs_dir = tmpdir / "doc"
        docs_dir.mkdir(exist_ok=True)
        web_conf = tmpdir / "mcweb.env"
        web_conf.write_text(
            """# mcweb runtime config

BACKUP_DIR=C:/old/backups
DISPLAY_TZ=UTC
MCWEB_ADMIN_PASSWORD_HASH=adminhash
MCWEB_REQUIRE_PASSWORD=true
MCWEB_SECRET_KEY=secret
MCWEB_SUPERADMIN_PASSWORD_HASH=superhash
MINECRAFT_ROOT_DIR=C:/old/root
SERVICE=minecraft
""",
            encoding="utf-8",
        )
        state = {
            "validate_superadmin_password": lambda password: password == "ok",
            "_ensure_csrf_token": lambda: "t",
            "DOCS_DIR": docs_dir,
            "WEB_CFG_VALUES": {
                "BACKUP_DIR": "C:/old/backups",
                "DISPLAY_TZ": "UTC",
                "MCWEB_ADMIN_PASSWORD_HASH": "adminhash",
                "MCWEB_SUPERADMIN_PASSWORD_HASH": "superhash",
                "MCWEB_SECRET_KEY": "secret",
                "MINECRAFT_ROOT_DIR": "C:/old/root",
                "SERVICE": "minecraft",
            },
            "APP_STATE_DB_PATH": tmpdir / "state.sqlite3",
            "get_device_name_map": lambda: {},
            "record_successful_password_ip": lambda: None,
        }

        panel_settings_routes.register_panel_settings_routes(app, state)
        client = app.test_client()

        with patch.object(
            panel_settings_routes.setup_queries_service,
            "validate_setup_request",
            return_value={"ok": True},
        ):
            response = client.post(
                "/panel-settings/paths",
                json={
                    "sudo_password": "ok",
                    "display_tz": "Asia/Manila",
                    "minecraft_root_dir": "C:/new/root",
                    "backup_dir": "C:/new/backups",
                    "create_backup_dir": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json() or {}
        self.assertTrue(body.get("ok"))
        defaults, web_conf_path, _app_dir = panel_settings_routes._load_env_defaults(state)
        self.assertEqual(web_conf_path, web_conf)
        self.assertEqual(defaults["DISPLAY_TZ"], "Asia/Manila")
        self.assertEqual(defaults["MINECRAFT_ROOT_DIR"], "C:/new/root")
        self.assertEqual(defaults["BACKUP_DIR"], "C:/new/backups")

    def test_device_map_save_persists_values_for_reload(self):
        app = __import__("flask").Flask(__name__)
        tmpdir = Path(tempfile.mkdtemp())
        docs_dir = tmpdir / "doc"
        docs_dir.mkdir(exist_ok=True)
        db_path = tmpdir / "state.sqlite3"
        state_store_service.initialize_state_db(db_path=db_path)
        state = {
            "validate_superadmin_password": lambda password: password == "ok",
            "_ensure_csrf_token": lambda: "t",
            "DOCS_DIR": docs_dir,
            "WEB_CFG_VALUES": {
                "DISPLAY_TZ": "UTC",
                "MCWEB_ADMIN_PASSWORD_HASH": "adminhash",
                "MCWEB_SUPERADMIN_PASSWORD_HASH": "superhash",
                "MCWEB_SECRET_KEY": "secret",
                "MINECRAFT_ROOT_DIR": "C:/root",
                "BACKUP_DIR": "C:/backups",
                "SERVICE": "minecraft",
            },
            "APP_STATE_DB_PATH": db_path,
            "DATA_DIR": tmpdir / "data",
            "get_device_name_map": lambda: {},
            "record_successful_password_ip": lambda: None,
        }

        panel_settings_routes.register_panel_settings_routes(app, state)
        client = app.test_client()
        save_response = client.post(
            "/panel-settings/device-map/save",
            json={
                "sudo_password": "ok",
                "rows": [
                    {"name": "krystal", "ip": "100.86.176.27", "owner": "jp"},
                    {"name": "krystal", "ip": "fd7a:115c:a1e0::9d39:b01b", "owner": "jp"},
                ],
            },
        )

        self.assertEqual(save_response.status_code, 200)
        rows = state_store_service.load_fallmap_rows(db_path)
        self.assertEqual(
            rows,
            [
                {"ip": "100.86.176.27", "device_name": "krystal", "owner": "jp"},
                {"ip": "fd7a:115c:a1e0::9d39:b01b", "device_name": "krystal", "owner": "jp"},
            ],
        )
        self.assertEqual(
            panel_settings_routes._load_device_fallmap_rows(state),
            rows,
        )

    def test_device_map_save_returns_error_when_readback_does_not_match(self):
        app = __import__("flask").Flask(__name__)
        tmpdir = Path(tempfile.mkdtemp())
        docs_dir = tmpdir / "doc"
        docs_dir.mkdir(exist_ok=True)
        db_path = tmpdir / "state.sqlite3"
        state_store_service.initialize_state_db(db_path=db_path)
        state = {
            "validate_superadmin_password": lambda password: password == "ok",
            "_ensure_csrf_token": lambda: "t",
            "DOCS_DIR": docs_dir,
            "WEB_CFG_VALUES": {
                "DISPLAY_TZ": "UTC",
                "MCWEB_ADMIN_PASSWORD_HASH": "adminhash",
                "MCWEB_SUPERADMIN_PASSWORD_HASH": "superhash",
                "MCWEB_SECRET_KEY": "secret",
                "MINECRAFT_ROOT_DIR": "C:/root",
                "BACKUP_DIR": "C:/backups",
                "SERVICE": "minecraft",
            },
            "APP_STATE_DB_PATH": db_path,
            "DATA_DIR": tmpdir / "data",
            "get_device_name_map": lambda: {},
            "record_successful_password_ip": lambda: None,
            "log_mcweb_exception": lambda *args, **kwargs: None,
        }

        panel_settings_routes.register_panel_settings_routes(app, state)
        client = app.test_client()

        with patch.object(
            panel_settings_routes,
            "_load_device_fallmap_rows",
            return_value=[{"ip": "100.86.176.27", "device_name": "old-name", "owner": ""}],
        ):
            response = client.post(
                "/panel-settings/device-map/save",
                json={
                    "sudo_password": "ok",
                    "rows": [
                        {"name": "krystal", "ip": "100.86.176.27", "owner": "jp"},
                    ],
                },
            )

        self.assertEqual(response.status_code, 500)
        body = response.get_json() or {}
        self.assertFalse(body.get("ok", True))
        self.assertIn("did not persist", body.get("message", ""))

    def test_panel_settings_api_state_returns_persisted_device_map(self):
        app = __import__("flask").Flask(__name__)
        tmpdir = Path(tempfile.mkdtemp())
        docs_dir = tmpdir / "doc"
        docs_dir.mkdir(exist_ok=True)
        db_path = tmpdir / "state.sqlite3"
        state_store_service.initialize_state_db(db_path=db_path)
        state_store_service.replace_fallmap_rows(
            db_path,
            [
                {"ip": "100.86.176.27", "device_name": "krystal", "owner": "jp"},
                {"ip": "fd7a:115c:a1e0::9d39:b01b", "device_name": "krystal", "owner": "jp"},
            ],
        )
        state = {
            "validate_superadmin_password": lambda password: password == "ok",
            "_ensure_csrf_token": lambda: "t",
            "DOCS_DIR": docs_dir,
            "WEB_CFG_VALUES": {
                "DISPLAY_TZ": "UTC",
                "MCWEB_ADMIN_PASSWORD_HASH": "adminhash",
                "MCWEB_SUPERADMIN_PASSWORD_HASH": "superhash",
                "MCWEB_SECRET_KEY": "secret",
                "MINECRAFT_ROOT_DIR": "C:/root",
                "BACKUP_DIR": "C:/backups",
                "CREATE_BACKUP_DIR": "false",
                "SERVICE": "minecraft",
            },
            "APP_STATE_DB_PATH": db_path,
            "get_device_name_map": lambda: {},
            "record_successful_password_ip": lambda: None,
        }
        panel_settings_routes.register_panel_settings_routes(app, state)
        client = app.test_client()

        response = client.get("/panel-settings/api/state")

        self.assertEqual(response.status_code, 200)
        body = response.get_json() or {}
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("device_map", {}).get("100.86.176.27"), "krystal")
        self.assertEqual(len(body.get("device_machines") or []), 1)


if __name__ == "__main__":
    unittest.main()
