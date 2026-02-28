Minecraft Web Dashboard Setup

This project is a Flask dashboard (`mcweb.py`) that controls a systemd Minecraft service, runs backups through `scripts/backup.sh`, and sends RCON commands.

1. Project layout

Recommended location:
/opt/Minecraft/webserverbyjp/
  app/
    core/
    routes/
    services/
    state/
  mcweb.py
  mcweb.env
  doc/mcweb.env.sample
  scripts/backup.sh
  data/state.txt
  data/session.txt
  templates/
  static/
  logs/
  doc/server_setup_doc.md
  tests/

Required files relative to `mcweb.py`:
- `mcweb.env`
- `doc/mcweb.env.sample` (template for generating local `mcweb.env`)
- `scripts/backup.sh`
- `templates/documentation.html`
- `doc/server_setup_doc.md`

2. Main configuration (`mcweb.env`)

`mcweb.py` and `scripts/backup.sh` load settings from `mcweb.env` in the same folder as `mcweb.py`.

Important keys:
- `SERVICE`
- `MCWEB_ADMIN_PASSWORD_HASH`
- `WEB_HOST`
- `WEB_PORT`
- `BACKUP_SCRIPT`
- `BACKUP_DIR`
- `CRASH_REPORTS_DIR`
- `MINECRAFT_LOGS_DIR`
- `MCWEB_LOG_DIR`
- `DATA_DIR`
- `DOCS_DIR`
- `RCON_HOST`
- `RCON_PORT`
- `DISPLAY_TZ`
- backup/idle/metrics interval keys
- `BACKUP_STATE_FILE`
- `DEBUG` (controls debug page availability at app boot)

Bootstrap tip:
- copy `doc/mcweb.env.sample` to `mcweb.env` and then fill local values.

3. Configure `server.properties`

`mcweb.py` and `backup.sh` search for `server.properties` in this order:
1) `/opt/Minecraft/server.properties`
2) `/opt/Minecraft/server/server.properties`
3) `<mcweb.py folder>/server.properties`
4) `<parent of mcweb.py folder>/server.properties`

Required values:
- `enable-rcon=true`
- `rcon.password=YOUR_PASSWORD`
- `rcon.port=25575`

Notes:
- `rcon.password` is used for RCON only.
- privileged dashboard actions validate against `MCWEB_ADMIN_PASSWORD_HASH` from `mcweb.env`.
- `rcon.port` from `server.properties` overrides base/default RCON port behavior.

4. `backup.sh` behavior

`scripts/backup.sh` reads tunables from `mcweb.env` (`BACKUP_DIR`, `AUTO_SNAPSHOT_DIR`, `BACKUP_STATE_FILE`, `RCON_HOST`, `RCON_PORT`, `DEBUG`).

World folder resolution:
- when `DEBUG=false`: world folder is derived from `server.properties` `level-name`
- when `DEBUG=true`: world folder is forced to `/opt/Minecraft/config`

Backup output is trigger-based:
- auto interval backup: incremental snapshot directory
  - default location: `<BACKUP_DIR>/snapshots/world_<timestamp>_auto/`
  - implementation: `rsync -a --delete` with `--link-dest` to previous `_auto` snapshot
- manual backup button: full zip `world_<timestamp>_manual.zip`
- session-end backup: full zip `world_<timestamp>_session_end.zip`

Optional override:
- `AUTO_SNAPSHOT_DIR` sets where auto snapshots are stored (default is `<BACKUP_DIR>/snapshots`).

Make executable:
`chmod +x /opt/Minecraft/webserverbyjp/scripts/backup.sh`

5. Debug mode behavior

`DEBUG` is read at app boot and controls debug route visibility:
- `DEBUG=true`: `/debug` and debug tools are available
- `DEBUG=false`: `/debug` and `/debug/*` return 404 and Debug nav link is hidden

Debug boot handling for `server.properties`:
- when `DEBUG=true`:
  - active `server.properties` is preserved as `server.properties.real` (same directory, if missing)
  - active `server.properties` is regenerated from `.real`
  - forced values are applied:
    - `level-name=debug_world`
    - `motd=debugging in progress`
- when `DEBUG=false` and `.real` exists:
  - active file is restored from `.real`
  - if active file still contains debug world/motd, it is archived to `data/server.properties.debug`

Debug action authentication:
- debug `server.properties` Apply requires admin password validation
- debug env editor Apply/Reset requires admin password validation
- debug Stop requires admin password validation

6. Install dependencies

Runtime dependencies:
- Python 3
- Flask (`pip install flask`)
- `mcrcon` (must be in PATH)
- `zip`
- `rsync` (required for auto snapshots)
- `sudo`, `systemd`

Example:
sudo apt update
sudo apt install -y python3 python3-pip zip rsync mcrcon nginx
python3 -m pip install flask

7. Run the dashboard

From the project folder:
`python3 mcweb.py`

Bind address and port come from `mcweb.env` (`WEB_HOST`, `WEB_PORT`).

8. Nginx reverse proxy (optional, no `:8080` in URL)

Example server block:

server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

Then reload Nginx:
sudo nginx -t
sudo systemctl reload nginx

9. Optional: run `mcweb.py` as a service

For production, run `mcweb.py` with a process manager (systemd, supervisor, etc.) so it restarts automatically.

10. Systemd + sudoers (recommended)

If `mcweb.py` runs under systemd and uses `sudo` for service/backup actions, add a sudoers rule (via `visudo`) for the service account.

Example:
marites ALL=(root) NOPASSWD: /bin/systemctl start minecraft, /bin/systemctl stop minecraft, /bin/systemctl restart minecraft, /bin/systemctl status minecraft, /opt/Minecraft/webserverbyjp/scripts/backup.sh

Notes:
- Use absolute paths for every command.
- Keep each command separated by commas.
- The app uses `sudo -n` for privileged commands, so required actions must be in sudoers with `NOPASSWD`.
- Verify your actual `systemctl` path (`/bin/systemctl` vs `/usr/bin/systemctl`) with:
  `command -v systemctl`

11. Tests

Current test files:
- `tests/test_config.py`
- `tests/test_file_utils.py`
- `tests/test_control_plane.py`

Run tests:
`python -m unittest discover -s tests -p "test_*.py"`

12. Safety and restore notes

Low storage protection:
- startup is blocked when storage is below configured safe threshold
- home page shows the low-storage warning immediately
- if storage drops low while server is running, an RCON warning is sent and emergency shutdown runs after 30 seconds
- emergency shutdown backup files use `_emergency` suffix

Backup reliability:
- backups use trigger-based suffixes (`_manual`, `_session_end`, `_emergency`, etc.)
- when `DEBUG=true`, backup filenames also append `_debug` (in addition to any existing suffix)
- backup flow restores `save-on` on abnormal exits to avoid leaving autosave disabled

Restore behavior:
- restore creates a new world directory named from the current world + timestamp
- restored data is applied to the new world directory
- `server.properties` `level-name` is switched to the new world directory name
- previous world path is recorded in `data/old_world.txt`
- pre-restore snapshot creation is required; restore is canceled if snapshot creation fails
- undo restore is available using the latest pre-restore snapshot

13. Maintenance page (cleanup)

Maintenance is scope-based and keeps separate rule/schedule/history metadata per scope:
- `backups`
- `stale_worlds`

Maintenance data files in `DATA_DIR`:
- `cleanup.json` (rules/schedules/meta/scopes)
- `cleanup history.json` (run history)
- `cleanup_non_normal.txt` (missed-run tracking)
- `logs/cleanup.log` (maintenance action logs)

Protected actions (admin password required):
- open rules edit mode
- save rules
- run rule cleanup
- manual cleanup

If password validation fails, action is rejected with `invalid_password`.

Rule behavior (backups):
- backup deletion uses AND semantics for enabled gates (age/count/space)
- count rule is per backup type (`session`, `manual`, `pre_restore`)

Dry-run behavior:
- dry-run for rule cleanup and manual cleanup returns preview data
- UI shows a Dry Run Results modal with:
  - files that would be deleted
  - reported errors/issues (if present)

History and audit UI:
- `Last changed by` shows device name if IP mapping exists, otherwise raw IP
- timestamps are rendered in a human-readable local format
- `Next run` is shown in the same readable time format

14. TODO

- [ ] Add automatic retention/cleanup for old restored world directories (using `data/old_world.txt` for visibility and safe pruning)
- [ ] Add UI surfacing for explicit debug-stop auth failures (`Password incorrect`) instead of generic stop failure
- [ ] Add integration tests for debug auth gates (`/debug/server-properties`, `/debug/env`, `/debug/stop`)
- [ ] Add end-to-end tests for low-storage startup block and emergency shutdown path
- [ ] Add restore dry-run validation mode to inspect zip structure and target paths before applying
