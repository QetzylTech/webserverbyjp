Minecraft Web Dashboard Setup

This project is a Flask dashboard (mcweb.py) that controls a systemd Minecraft service named minecraft, runs backups, and sends RCON commands.

1. Place the files

Recommended location:
/opt/Minecraft/webserverbyjp/
  mcweb.py
  backup.sh
  state.txt
  session.txt

Required local files relative to mcweb.py:
backup.sh (same folder as mcweb.py)
state.txt (same folder as mcweb.py)
session.txt (same folder as mcweb.py)

2. Configure server.properties

mcweb.py searches for server.properties in this order:
1) /opt/Minecraft/server.properties
2) /opt/Minecraft/server/server.properties
3) <mcweb.py folder>/server.properties
4) <parent of mcweb.py folder>/server.properties

Your server.properties must include:
enable-rcon=true
rcon.password=YOUR_PASSWORD
rcon.port=25575

rcon.password is also used by this app as the password check for privileged actions in the UI.

3. Configure backup.sh

Edit backup.sh to match your environment:
RCON_PASS should match rcon.password
WORLD_DIR should point to your actual world folder
BACKUP_DIR should be where you want .zip backups stored
STATE_FILE should match mcweb.py state file path

Recommended STATE_FILE value:
STATE_FILE="/opt/Minecraft/webserverbyjp/state.txt"

Then make it executable:
chmod +x /opt/Minecraft/webserverbyjp/backup.sh

4. Install dependencies

Install runtime dependencies on the server:
Python 3
Flask (pip install flask)
mcrcon (must be in PATH, for example /usr/bin/mcrcon)
zip
sudo, systemd

Example:
sudo apt update
sudo apt install -y python3 python3-pip zip mcrcon nginx
python3 -m pip install flask

5. Ensure service names and paths match

In mcweb.py defaults:
SERVICE = "minecraft"
BACKUP_DIR = Path("/home/marites/backups")

Change these if your server uses different names or paths.

6. Run the dashboard app

From the project folder:
python3 mcweb.py

Flask listens on port 8080 internally.

7. Nginx reverse proxy (no :8080 in URL)

If you are using Nginx, proxy web traffic to Flask on 127.0.0.1:8080.
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

Access URL (without port):
http://<server-ip>/

8. Optional: run mcweb.py as a service

For production, run mcweb.py with a process manager (systemd, supervisor, etc.) so it restarts automatically.


To do:

Add rate limiting to start and backup functions  to prevent spamming
Add proper error messages
Add log selection
    Minecraft Logs
    Backup Logs
    Server Event Logs
Add link to server documentation
Impprove client side performance (Fix browser tab lag at server startup)