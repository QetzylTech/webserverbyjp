Minecraft Web Dashboard Setup

This project is a Flask dashboard (`mcweb.py`) that controls a systemd Minecraft service, runs backups through `scripts/backup.sh`, and sends RCON commands.

1. Project layout

Recommended location:
/opt/Minecraft/webserverbyjp/
  mcweb.py
  web.conf
  scripts/backup.sh
  data/state.txt
  data/session.txt
  doc/documentation.html
  doc/README.md
  logs/
  static/
  templates/

Required files relative to `mcweb.py`:
- `web.conf`
- `scripts/backup.sh`
- `data/state.txt`
- `data/session.txt`
- `doc/documentation.html`
- `doc/README.md`

2. Main configuration (`web.conf`)

`mcweb.py` and `scripts/backup.sh` load settings from `web.conf` in the same folder as `mcweb.py`.

Important keys:
- `SERVICE`
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
- `WORLD_DIR`, `STATE_FILE` (for `scripts/backup.sh`)

3. Configure `server.properties`

`mcweb.py`/`backup.sh` search for `server.properties` in this order:
1) `/opt/Minecraft/server.properties`
2) `/opt/Minecraft/server/server.properties`
3) `<mcweb.py folder>/server.properties`
4) `<parent of mcweb.py folder>/server.properties`

Required values:
- `enable-rcon=true`
- `rcon.password=YOUR_PASSWORD`
- `rcon.port=25575`

Notes:
- `rcon.password` is also used by the dashboard as the privileged-action password check.
- `rcon.port` from `server.properties` overrides base/default RCON port behavior.

4. `backup.sh` setup

`scripts/backup.sh` reads tunables from `web.conf` (for example `WORLD_DIR`, `BACKUP_DIR`, `STATE_FILE`, `RCON_HOST`, `RCON_PORT`).

Make executable:
`chmod +x /opt/Minecraft/webserverbyjp/scripts/backup.sh`

5. Install dependencies

Install runtime dependencies:
- Python 3
- Flask (`pip install flask`)
- `mcrcon` (must be in PATH)
- `zip`
- `sudo`, `systemd`

Example:
sudo apt update
sudo apt install -y python3 python3-pip zip mcrcon nginx
python3 -m pip install flask

6. Run the dashboard

From the project folder:
`python3 mcweb.py`

Bind address and port come from `web.conf` (`WEB_HOST`, `WEB_PORT`).

7. Nginx reverse proxy (optional, no `:8080` in URL)

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

8. Optional: run `mcweb.py` as a service

For production, run `mcweb.py` with a process manager (systemd, supervisor, etc.) so it restarts automatically.

9. Systemd + sudoers (recommended)

If `mcweb.py` runs under systemd and uses `sudo` for service/backup actions, add a sudoers rule (via `visudo`) for the service account.

Example:
marites ALL=(root) NOPASSWD: /bin/systemctl start minecraft, /bin/systemctl stop minecraft, /bin/systemctl restart minecraft, /bin/systemctl status minecraft, /opt/Minecraft/webserverbyjp/scripts/backup.sh

Notes:
- Use absolute paths for every command.
- Keep each command separated by commas.
- Verify your actual `systemctl` path (`/bin/systemctl` vs `/usr/bin/systemctl`) with:
  `command -v systemctl`
