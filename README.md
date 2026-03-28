Minecraft Web Dashboard Setup

This project is a Flask dashboard (`mcweb.py`) that controls a Minecraft service, runs backups through `scripts/backup.sh`, and sends RCON commands.
The app now uses a platform call layer (`app/platform/*`) for OS-specific command execution (service control, backup script invocation, etc.).
Runtime app data lives in the project-root `data/` directory, not under `app/`.

Documentation index:
- High-level product requirements: `doc/project requirements.txt`
- Architecture contract: `ARCHITECTURE.md`
- CI gate order and commands: `doc/CI_QUALITY_GATES.md`
- PR acceptance checklist (priority-ordered): `doc/PR_ACCEPTANCE_CHECKLIST.md`

Roadmap / TODO
- [ ] Remove Linux systemd dependency (and equivalents on other OSes) by invoking `run.sh` or `server.jar` directly.
- [ ] Add a Settings page:
  - password settings
  - upload device lists
  - per-device rulesets
- [ ] Add a World Management page:
  - full root directory browsing (files/folders)
  - upload/rename/delete/create files and folders
  - config and `server.properties` editor
- [ ] Change Minecraft directory structure:
  - root folder named `minecraft`
  - subfolders per Minecraft version
  - each version has a full server instance
  - manage/start each version independently (hardware-limited)
- [ ] Add explain error button to log viewers
  -  possible llm based explainer running client side
- [ ] Add nginx reverse proxy with wol and boot animation
  - if main flask app is online, do reverse proxy
  - if main flask app is offline, send wol and show booting animation
  - once main flask app is booted, apply reverse proxy
- [ ] Add Google Drive integration
  - Google Drive API key or auth key saved in env file
  - Saves copy of backups to Google drive in addition to local
  - Enable restoring a world from the drive
  - Have manual cleanup on Google Drive
  - Google drive has only 15gb capacity so it should store only the n latest number of backups that can fit and then just delete the oldest as new backups are uploaded.
- [ ] Layout Change
Known bugs:
  - Page state isn't preserved. (Require  that for each client, the tab, pane, scope or scroll location for each page is saved so that when navigating away ang back into the page, the client sees where they left off)
  - After pressing start, server status stays at starting and the live server console logs is not updating. I think this is the cause: since the server status has a trigger from the logs where it waits for a message to change into running ir shutting down, it gets stuck there. Server seems to be running since cpu metrics look alive and clients can connect to it. Basically status gets stuck at starting and shutting down. Proposed fix: Fix the live logs update since they are not coming. 
  - The global Content area/ card input style is not applying to the inputs in the cleanup rules pane contents for both scopes
  - The password incorrect image is currently showing even in initial password prompt. It should only show when password entered is incorrect. Incorrect password/ password retry and initial password prompt(the original untouched password prompt modal) are separate types of modals

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
  data/app_state.sqlite3
  data/state.txt
  data/session.txt
  data/properties/
  data/old_app_data/
  templates/
  static/
  logs/
  doc/server_setup_doc.md
  tests/

Required files relative to `mcweb.py`:
- `mcweb.env`
- `doc/mcweb.env.sample` (template for generating local `mcweb.env`)
- `scripts/backup.sh`
- `templates/app_shell.html`
- `templates/fragments/*.html`
- `doc/server_setup_doc.md`

Runtime state location:
- active runtime data is stored in the project-root `data/`
- do not place active runtime files under `app/data`

2. Main configuration (`mcweb.env`)

`mcweb.py` and `scripts/backup.sh` load settings from `mcweb.env` in the same folder as `mcweb.py`.

Important keys:
- `SERVICE`
- `MCWEB_ADMIN_PASSWORD_HASH`
- `PORT`
- `MINECRAFT_ROOT_DIR`
- `BACKUP_DIR`
- `DISPLAY_TZ`
- `MCWEB_PROCESS_ROLE`
- backup/idle/metrics interval keys

Derived from `MINECRAFT_ROOT_DIR`:
- world directory (from `server.properties` `level-name`, debug fallback `<MINECRAFT_ROOT_DIR>/config`)
- crash reports directory (`<MINECRAFT_ROOT_DIR>/crash-reports`)
- minecraft logs directory (`<MINECRAFT_ROOT_DIR>/logs`)

Hardcoded relative to the project root (same folder as `mcweb.py`, not env-configurable):
- backup script: `./scripts/backup.sh`
- app logs directory: `./logs`
- app data directory: `./data`
- docs directory: `./doc`
- backup state file: `./data/state.txt`

Notes:
- the active runtime data directory is the root `data/` folder
- `app/data` is not used for live runtime state

SQLite state database path is fixed (not env-configurable):
- `/opt/Minecraft/webserverbyjp/data/app_state.sqlite3` (or `<mcweb.py folder>/data/app_state.sqlite3`)

Bootstrap tip:
- copy `doc/mcweb.env.sample` to `mcweb.env` and then fill local values.

3. Configure `server.properties`

`mcweb.py` and `backup.sh` search for `server.properties` in this order:
1) `<MINECRAFT_ROOT_DIR>/server.properties`
2) `<MINECRAFT_ROOT_DIR>/server/server.properties`
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

4. Backup behavior

`scripts/backup.sh` reads tunables from `mcweb.env` (`MINECRAFT_ROOT_DIR`, `BACKUP_DIR`, `AUTO_SNAPSHOT_DIR`, `RCON_HOST`, `RCON_PORT`, `DEBUG`).

World folder resolution:
- when `DEBUG=false`: world folder is derived from `server.properties` `level-name`
- when `DEBUG=true`: world folder is forced to `<MINECRAFT_ROOT_DIR>/config` (default `/opt/Minecraft/config`)

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

Execution path:
- app logic calls a generic backup runner (`run_backup_script(...)`)
- the platform module (`app/platform/calls_*.py`) executes the script with OS-specific command handling

5. Debug mode behavior

`DEBUG` is read at app boot and controls debug route visibility:
- `DEBUG=true`: `/debug` and debug tools are available
- `DEBUG=false`: `/debug` and `/debug/*` return 404 and Debug nav link is hidden

Debug boot handling for `server.properties`:
- when `DEBUG=true`:
  - active `server.properties` is snapshotted to `<project root>/data/properties/server.properties.<timestamp>.bak`
  - forced values are applied:
    - `level-name=debug_world`
    - `motd=debugging in progress`
- when `DEBUG=false` and active file is debug-provisioned:
  - active file is restored from `<project root>/data/properties/debug_properties.state` (`last_backup`) or latest `server.properties.*.bak`

Debug action authentication:
- debug `server.properties` Apply requires admin password validation
- debug env editor Apply/Reset requires admin password validation
- debug Stop requires admin password validation

6. Install dependencies

Runtime dependencies:
- Python 3
- Flask (`pip install flask`)
- `mcrcon`
git clone https://github.com/Tiiffi/mcrcon.git
cd mcrcon
make
sudo mv mcrcon /usr/local/bin/
mcrcon -H 127.0.0.1 -P 25575 -p password
- `zip`
- `rsync` (required for auto snapshots)
- `sudo`, `systemd`
- `nginx` (optional to use port 80 and redirect to 8080)

Example:
sudo apt update
sudo apt install -y python3 python3-pip zip rsync mcrcon nginx
python3 -m pip install flask

7. Run the dashboard

From the project folder:
`python3 mcweb.py`

Equivalent entrypoint:
`python -m app.main`

Bind behavior:
- host is fixed to `0.0.0.0`
- port comes from `mcweb.env` via `PORT`

Process role:
- `MCWEB_PROCESS_ROLE=all|web|worker` (default `all`)
- `web`: routes/API only
- `worker`: background loops (metrics/reconciler/maintenance precompute/index refresh)
- `all`: both in one process

8. Live update model

The UI uses a shell-owned live-update model:
- `/metrics-stream` (SSE): the continuous metrics channel used by the shell
- `/notifications-stream` (SSE): shell-owned notification delivery
- `/operation-stream` (SSE): shell-owned operation acceptance/progress/completion updates
- `/log-stream/<source>` (SSE): initial snapshot plus batched Home log delivery for the active source
- `/file-page-stream/<page_name>` and `/log-files-stream/<source>` (SSE): pushed file-list inventory updates for file-browser pages
- `/maintenance-stream` (SSE): pushed cleanup state updates for the maintenance page
- `/stream/restore_logs?job_id=...` (SSE): restore-progress log and status delivery
- one-shot HTTP endpoints remain for explicit user-driven reads such as viewed-file content, README/document content, downloads, and action POSTs, with fetch-based state reads kept only as compatibility fallbacks

The current dashboard runtime no longer polls live metrics, operation status, restore status, maintenance state, or file-list state from the browser. Metrics follow the collector cadence, and live log streams deliver an initial snapshot plus batched updates instead of one SSE event per line.

Status transition timing note:
- start/stop button handlers set intent immediately, but the visible dashboard state is still read through cached observed-state and streamed metrics snapshots
- default cache values (`_OBSERVED_OPS_CACHE_TTL_SECONDS=1.5`, `_OBSERVED_STATE_CACHE_TTL_SECONDS=1.25`, and active metrics collection `1.0s`) can add about 1 to 2 seconds before `Starting` or `Shutting Down` appears
- `Running` can take a bit longer because the final state also waits on live service status plus Minecraft readiness/RCON checks

9. Offline/recovery behavior

The app includes an offline shell + recovery flow:
- Service worker route: `/sw.js`
- Static offline fallback page: `static/offline.html`
- Client recovery helper: `static/offline_recovery.js`

Behavior:
- if server/network is unavailable, a red offline banner appears
- when signal is restored, banner turns green (`Signal restored. Reconnecting...`) for ~1 second, then page reloads
- service worker caches static/offline assets; dynamic HTML pages are not cached

9.1 Frontend shell runtime

Current architecture:
- full page loads render `templates/app_shell.html`
- shell navigation requests send `X-MCWEB-Fragment: 1` and receive fragment-only HTML
- `static/app_shell.js` intercepts internal navigation, swaps fragment HTML into `#mcweb-app-content`, and mounts/unmounts page modules through `MCWebPageModules`
- the shell owns theme preference wiring, sidebar nav wiring, metrics SSE, persistent client identity, shared home-log state, and cross-page caches for README/file/log/maintenance data
- page scripts own page-specific DOM wiring, page-specific SSE subscriptions, and teardown only

Guardrails:
- do not duplicate shell boot concerns such as theme or nav binding inside page scripts
- do not add a second continuous metrics owner; live metrics stay under the shell SSE runtime
- keep heavy page data lazy and cacheable, and tear down page-specific listeners, streams, and timers on unmount

10. Nginx reverse proxy (optional, no `:8080` in URL)

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

11. Optional: run `mcweb.py` as a service

For production, run `mcweb.py` with a process manager (systemd, supervisor, etc.) so it restarts automatically.

12. Systemd + sudoers (recommended)

If `mcweb.py` runs under systemd and uses `sudo` for service/backup actions, add a sudoers rule (via `visudo`) for the service account.

Example:
marites ALL=(root) NOPASSWD: /bin/systemctl start minecraft, /bin/systemctl start --no-block minecraft, /bin/systemctl stop minecraft, /bin/systemctl restart minecraft, /bin/systemctl status minecraft, /bin/systemctl is-active minecraft, /home/marites/webserverbyjp/scripts/backup.sh

Notes:
- Use absolute paths for every command.
- Keep each command separated by commas.
- The app uses `sudo -n` for privileged commands, so required actions must be in sudoers with `NOPASSWD`.
- Verify your actual `systemctl` path (`/bin/systemctl` vs `/usr/bin/systemctl`) with:
  `command -v systemctl`

13. Tests

Run full suite:
`python -m pytest -q tests`

If `pytest` is unavailable:
`python -m unittest discover -s tests -p "test_*.py"`

Notable coverage areas include:
- control-plane operation semantics/idempotency/reconciliation
- snapshot backup/restore/download routes
- maintenance candidate scan/state behavior
- performance optimization guards
- template/route contract checks

14. Safety and restore notes

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

15. Maintenance page (cleanup)

Maintenance is scope-based and keeps separate rule/schedule/history metadata per scope:
- `backups`
- `stale_worlds`

Maintenance data in the project-root `data/` directory:
- `app_state.sqlite3` (structured records: users, device map, cleanup config/history)
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

16. Quality gates and review contract

- High-level product requirements: `doc/project requirements.txt`
- Architecture contract: `ARCHITECTURE.md`
- CI gate order and commands: `doc/CI_QUALITY_GATES.md`
- PR acceptance checklist (priority-ordered): `doc/PR_ACCEPTANCE_CHECKLIST.md`
