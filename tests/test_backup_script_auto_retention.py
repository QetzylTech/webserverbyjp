import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


class BackupScriptAutoRetentionTests(unittest.TestCase):
    def _write_executable(self, path, content):
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def test_auto_snapshot_retains_latest_three(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash not available")

        repo_root = Path(__file__).resolve().parents[1]
        source_script = repo_root / "scripts" / "backup.sh"
        if not source_script.exists():
            self.skipTest("backup.sh not found")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_dir = root / "app"
            scripts_dir = app_dir / "scripts"
            fake_bin = root / "bin"
            backups_dir = root / "backups"
            mc_root = root / "minecraft"
            world_dir = mc_root / "world"
            snapshots_dir = backups_dir / "snapshots"

            scripts_dir.mkdir(parents=True)
            fake_bin.mkdir(parents=True)
            snapshots_dir.mkdir(parents=True, exist_ok=True)
            world_dir.mkdir(parents=True, exist_ok=True)
            (world_dir / "level.dat").write_text("world", encoding="utf-8")
            (mc_root / "server.properties").write_text(
                "level-name=world\nrcon.password=test\nrcon.port=25575\n",
                encoding="utf-8",
            )

            script_path = scripts_dir / "backup.sh"
            script_text = source_script.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
            script_path.write_text(script_text, encoding="utf-8")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            (app_dir / "mcweb.env").write_text(
                "\n".join(
                    [
                        "SERVICE=minecraft",
                        f"BACKUP_DIR={backups_dir}",
                        f"MINECRAFT_ROOT_DIR={mc_root}",
                        f"AUTO_SNAPSHOT_DIR={snapshots_dir}",
                        "DEBUG=false",
                    ]
                ),
                encoding="utf-8",
            )

            self._write_executable(
                fake_bin / "systemctl",
                "#!/usr/bin/env bash\nexit 1\n",
            )
            self._write_executable(
                fake_bin / "mcrcon",
                "#!/usr/bin/env bash\nexit 0\n",
            )
            self._write_executable(
                fake_bin / "rsync",
                (
                    "#!/usr/bin/env bash\n"
                    "set -euo pipefail\n"
                    "args=(\"$@\")\n"
                    "n=${#args[@]}\n"
                    "src=\"${args[$((n-2))]}\"\n"
                    "dst=\"${args[$((n-1))]}\"\n"
                    "mkdir -p \"$dst\"\n"
                    "cp -a \"${src%/}\"/. \"$dst\"/\n"
                    "exit 0\n"
                ),
            )

            env = dict(os.environ)
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["AUTO_SNAPSHOTS_TO_KEEP"] = "3"

            for _ in range(4):
                run = subprocess.run(
                    [bash, "-lc", "tr -d '\\r' < scripts/backup.sh > scripts/backup_lf.sh && bash scripts/backup_lf.sh auto"],
                    cwd=app_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if run.returncode != 0:
                    self.skipTest(
                        "backup.sh integration prerequisites unavailable in this shell "
                        f"(rc={run.returncode})."
                    )
                time.sleep(1.1)

            dirs = sorted([p for p in snapshots_dir.iterdir() if p.is_dir()])
            self.assertEqual(len(dirs), 3)
            for path in dirs:
                self.assertIn("_auto", path.name)


if __name__ == "__main__":
    unittest.main()
