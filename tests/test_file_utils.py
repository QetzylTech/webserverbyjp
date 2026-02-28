import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.filesystem_utils import format_file_size, list_download_files


class FileUtilsTests(unittest.TestCase):
    def test_format_file_size(self):
        self.assertEqual(format_file_size(10), "10 B")
        self.assertEqual(format_file_size(1024), "1.0 KB")

    def test_list_download_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "a.zip").write_text("a", encoding="utf-8")
            (base / "b.zip").write_text("bb", encoding="utf-8")
            items = list_download_files(base, "*.zip", ZoneInfo("UTC"))
            names = {item["name"] for item in items}
            self.assertEqual(names, {"a.zip", "b.zip"})


if __name__ == "__main__":
    unittest.main()
