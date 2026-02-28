import tempfile
import unittest
from pathlib import Path

from app.core.web_config import WebConfig


class WebConfigTests(unittest.TestCase):
    def test_reads_basic_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "mcweb.env"
            conf.write_text(
                "\n".join(
                    [
                        "SERVICE=minecraft",
                        "WEB_PORT=8080",
                        "BACKUP_INTERVAL_HOURS=3.5",
                        "BACKUP_SCRIPT=./scripts/backup.sh",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = WebConfig(conf, root)
            self.assertEqual(cfg.get_str("SERVICE", "x"), "minecraft")
            self.assertEqual(cfg.get_int("WEB_PORT", 0), 8080)
            self.assertEqual(cfg.get_float("BACKUP_INTERVAL_HOURS", 0.0), 3.5)
            self.assertEqual(cfg.get_path("BACKUP_SCRIPT", root / "none.sh"), root / "scripts" / "backup.sh")


if __name__ == "__main__":
    unittest.main()
