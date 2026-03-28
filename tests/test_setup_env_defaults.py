import unittest
from pathlib import Path

from app.services.setup_env_defaults import ENV_DEFAULTS


class SetupEnvDefaultsTests(unittest.TestCase):
    def test_file_page_cache_refresh_default_matches_documented_sample(self):
        self.assertEqual("15", ENV_DEFAULTS["FILE_PAGE_CACHE_REFRESH_SECONDS"])
        sample = (Path(__file__).resolve().parents[1] / "doc" / "mcweb.env.sample").read_text(encoding="utf-8")
        self.assertIn("FILE_PAGE_CACHE_REFRESH_SECONDS=15", sample)


if __name__ == "__main__":
    unittest.main()
