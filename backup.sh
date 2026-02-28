#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/backup.log"

RCON_PASS="SuperCute"
WORLD_DIR="/opt/Minecraft/The Server"
# WORLD_DIR="/opt/Minecraft/config"
BACKUP_DIR="/home/marites/backups"
DATE=$(date +"%Y-%m-%d_%H-%M-%S")
STATE_FILE="/opt/Minecraft/webserverbyjp/state.txt"

mkdir -p "$LOG_DIR"
exec >> "$LOG_FILE" 2>&1
echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup run started"

# Mark backup as running and always clear state on script exit.
echo "true" > "$STATE_FILE"
trap 'echo "false" > "$STATE_FILE"; echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup run finished"' EXIT

mkdir -p "$BACKUP_DIR"

# Notify players that backup is starting
mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "say Backup starting! Server may lag for a few seconds."

# Force world save and disable writes
mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "save-all"
mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "save-off"

# Give disk a moment to flush
sleep 5

# Create zip backup
if zip -r "$BACKUP_DIR/world_$DATE.zip" "$WORLD_DIR"; then
    # Backup succeeded
    mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "say Backup completed successfully! Saved to $BACKUP_DIR/world_$DATE.zip"
else
    # Backup failed
    mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "say Backup failed! Check server logs."
fi

# Re-enable saving
mcrcon -H 127.0.0.1 -P 25575 -p "$RCON_PASS" "save-on"
