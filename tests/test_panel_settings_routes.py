import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
