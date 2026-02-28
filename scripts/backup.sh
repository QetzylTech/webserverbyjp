#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WEB_CONF="$APP_DIR/mcweb.env"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/backup.log"

WORLD_DIR=""
BACKUP_DIR="/home/marites/backups"
DATE=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_TRIGGER="${1:-manual}"
BACKUP_SUFFIX=""
BACKUP_STATE_FILE="$APP_DIR/data/state.txt"
RCON_HOST="127.0.0.1"

SERVER_PROPERTIES_CANDIDATES=(
  "/opt/Minecraft/server.properties"
  "/opt/Minecraft/server/server.properties"
  "$APP_DIR/server.properties"
  "$APP_DIR/../server.properties"
)

RCON_PASS=""
RCON_PORT="25575"

normalize_path() {
  local p="$1"
  if [[ -z "$p" ]]; then
    echo ""
    return
  fi
  if [[ "$p" == /* ]]; then
    echo "$p"
  else
    echo "$APP_DIR/$p"
  fi
}

load_web_conf() {
  if [[ ! -f "$WEB_CONF" ]]; then
    return
  fi
  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    local line trimmed key value
    line="${raw_line%$'\r'}"
    trimmed="${line#"${line%%[![:space:]]*}"}"
    if [[ -z "$trimmed" || "$trimmed" == \#* ]]; then
      continue
    fi
    if [[ "$trimmed" != *=* ]]; then
      continue
    fi
    key="${trimmed%%=*}"
    value="${trimmed#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi
    case "$key" in
      WORLD_DIR) WORLD_DIR="$value" ;;
      BACKUP_DIR) BACKUP_DIR="$value" ;;
      BACKUP_STATE_FILE) BACKUP_STATE_FILE="$value" ;;
      RCON_HOST) RCON_HOST="$value" ;;
      RCON_PORT) RCON_PORT="$value" ;;
    esac
  done < "$WEB_CONF"
}

load_web_conf
WORLD_DIR="$(normalize_path "$WORLD_DIR")"
BACKUP_DIR="$(normalize_path "$BACKUP_DIR")"
BACKUP_STATE_FILE="$(normalize_path "$BACKUP_STATE_FILE")"

if [[ -z "$WORLD_DIR" ]]; then
  echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup aborted: WORLD_DIR is not set in mcweb.env"
  exit 1
fi

case "$BACKUP_TRIGGER" in
  auto) BACKUP_SUFFIX="_auto" ;;
  manual) BACKUP_SUFFIX="_manual" ;;
  session_end) BACKUP_SUFFIX="_session_end" ;;
  *)
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup aborted: invalid backup trigger '$BACKUP_TRIGGER'"
    exit 1
    ;;
esac

read_rcon_config() {
  local props=""
  local candidate=""
  for candidate in "${SERVER_PROPERTIES_CANDIDATES[@]}"; do
    if [[ -f "$candidate" ]]; then
      props="$candidate"
      break
    fi
  done

  if [[ -z "$props" ]]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] server.properties not found; cannot read rcon.password"
    return 1
  fi

  RCON_PASS=$(sed -n 's/^[[:space:]]*rcon\.password[[:space:]]*=[[:space:]]*//p' "$props" | tail -n 1 | tr -d '\r')
  local parsed_port
  parsed_port=$(sed -n 's/^[[:space:]]*rcon\.port[[:space:]]*=[[:space:]]*//p' "$props" | tail -n 1 | tr -d '\r')

  if [[ -n "$parsed_port" ]]; then
    RCON_PORT="$parsed_port"
  fi

  if [[ -z "$RCON_PASS" ]]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] rcon.password missing in $props"
    return 1
  fi

  return 0
}

mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$BACKUP_STATE_FILE")"
exec >> "$LOG_FILE" 2>&1
echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup run started"

if ! read_rcon_config; then
  echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup aborted: unable to load RCON credentials"
  exit 1
fi

# Mark backup as running and always clear state on script exit.
echo "true" > "$BACKUP_STATE_FILE"
trap 'echo "false" > "$BACKUP_STATE_FILE"; echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup run finished"' EXIT

mkdir -p "$BACKUP_DIR"

# Notify players that backup is starting
mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "say Backup starting! Server may lag for a few seconds."

# Force world save and disable writes
mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "save-all"
mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "save-off"

# Give disk a moment to flush
sleep 5

# Create zip backup
BACKUP_ZIP="$BACKUP_DIR/world_${DATE}${BACKUP_SUFFIX}.zip"

if zip -r "$BACKUP_ZIP" "$WORLD_DIR"; then
    # Backup succeeded
    mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "say Backup completed successfully! Saved to $BACKUP_ZIP"
else
    # Backup failed
    mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "say Backup failed! Check server logs."
fi

# Re-enable saving
mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "save-on"
