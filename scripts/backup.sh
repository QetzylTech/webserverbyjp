#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WEB_CONF="$APP_DIR/mcweb.env"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/backup.log"

BACKUP_DIR="/home/marites/backups"
DATE=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_TRIGGER="${1:-manual}"
BACKUP_SUFFIX=""
BACKUP_STATE_FILE="$APP_DIR/data/state.txt"
RCON_HOST="127.0.0.1"
AUTO_SNAPSHOT_DIR=""
WORLD_DIR=""
DEBUG_MODE="false"
DEBUG_WORLD_DIR="/opt/Minecraft/config"
WORLD_SAVE_DISABLED="false"
WORLD_NAME="world"
WORLD_NAME_FROM_PROPS=""

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

sanitize_filename_component() {
  local raw="$1"
  local cleaned
  cleaned="$(echo "${raw:-}" | sed -E 's/[[:space:]]+/_/g' | tr -cd '[:alnum:]_.-' | sed -E 's/_+/_/g; s/^_+//; s/_+$//')"
  if [[ -z "$cleaned" ]]; then
    cleaned="world"
  fi
  echo "$cleaned"
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
      BACKUP_DIR) BACKUP_DIR="$value" ;;
      BACKUP_STATE_FILE) BACKUP_STATE_FILE="$value" ;;
      RCON_HOST) RCON_HOST="$value" ;;
      RCON_PORT) RCON_PORT="$value" ;;
      AUTO_SNAPSHOT_DIR) AUTO_SNAPSHOT_DIR="$value" ;;
      DEBUG) DEBUG_MODE="$value" ;;
    esac
  done < "$WEB_CONF"
}

to_lower() {
  echo "$1" | tr '[:upper:]' '[:lower:]'
}

is_true() {
  local v
  v="$(to_lower "${1:-}")"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "on" ]]
}

load_web_conf
BACKUP_DIR="$(normalize_path "$BACKUP_DIR")"
BACKUP_STATE_FILE="$(normalize_path "$BACKUP_STATE_FILE")"
if [[ -z "$AUTO_SNAPSHOT_DIR" ]]; then
  AUTO_SNAPSHOT_DIR="$BACKUP_DIR/snapshots"
else
  AUTO_SNAPSHOT_DIR="$(normalize_path "$AUTO_SNAPSHOT_DIR")"
fi

if is_true "$DEBUG_MODE"; then
  WORLD_DIR="$DEBUG_WORLD_DIR"
fi

case "$BACKUP_TRIGGER" in
  auto) BACKUP_SUFFIX="_auto" ;;
  manual) BACKUP_SUFFIX="_manual" ;;
  session_end) BACKUP_SUFFIX="_session_end" ;;
  emergency) BACKUP_SUFFIX="_emergency" ;;
  *)
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup aborted: invalid backup trigger '$BACKUP_TRIGGER'"
    exit 1
    ;;
esac

if is_true "$DEBUG_MODE"; then
  BACKUP_SUFFIX="${BACKUP_SUFFIX}_debug"
fi

read_server_properties_config() {
  local props=""
  local candidate=""
  local level_name=""
  for candidate in "${SERVER_PROPERTIES_CANDIDATES[@]}"; do
    if [[ -f "$candidate" ]]; then
      props="$candidate"
      break
    fi
  done

  if [[ -z "$props" ]]; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] server.properties not found; cannot read rcon/password or level-name"
    return 1
  fi

  RCON_PASS=$(sed -n 's/^[[:space:]]*rcon\.password[[:space:]]*=[[:space:]]*//p' "$props" | tail -n 1 | tr -d '\r')
  local parsed_port
  parsed_port=$(sed -n 's/^[[:space:]]*rcon\.port[[:space:]]*=[[:space:]]*//p' "$props" | tail -n 1 | tr -d '\r')
  level_name=$(sed -n 's/^[[:space:]]*level-name[[:space:]]*=[[:space:]]*//p' "$props" | tail -n 1 | tr -d '\r')
  WORLD_NAME_FROM_PROPS="$level_name"
  if ! is_true "$DEBUG_MODE"; then
    if [[ -z "$level_name" ]]; then
      echo "[$(date +"%Y-%m-%d %H:%M:%S")] level-name missing in $props"
      return 1
    fi
    if [[ "$level_name" == /* ]]; then
      WORLD_DIR="$level_name"
    else
      WORLD_DIR="$(cd "$(dirname "$props")" && pwd)/$level_name"
    fi
  fi

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

cleanup() {
  local exit_code=$?
  # If save-off was issued, always try to restore save-on on any exit path.
  if [[ "$WORLD_SAVE_DISABLED" == "true" ]]; then
    mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "save-on" >/dev/null 2>&1 || true
    WORLD_SAVE_DISABLED="false"
  fi
  echo "false" > "$BACKUP_STATE_FILE"
  echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup run finished (exit=$exit_code)"
}

if ! read_server_properties_config; then
  echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup aborted: unable to load server.properties settings"
  exit 1
fi

if is_true "$DEBUG_MODE"; then
  echo "[$(date +"%Y-%m-%d %H:%M:%S")] debug mode enabled: forcing WORLD_DIR='$WORLD_DIR'"
fi

if [[ ! -d "$WORLD_DIR" ]]; then
  echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup aborted: world directory '$WORLD_DIR' does not exist"
  exit 1
fi

if [[ -n "$WORLD_NAME_FROM_PROPS" ]]; then
  WORLD_NAME="$(sanitize_filename_component "$WORLD_NAME_FROM_PROPS")"
else
  WORLD_NAME="$(sanitize_filename_component "$(basename "$WORLD_DIR")")"
fi

# Mark backup as running and always clear state on script exit.
echo "true" > "$BACKUP_STATE_FILE"
trap cleanup EXIT INT TERM

mkdir -p "$BACKUP_DIR"

# Notify players that backup is starting
mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "say Backup starting! Server may lag for a few seconds."

# Force world save and disable writes
mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "save-all"
WORLD_SAVE_DISABLED="true"
mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "save-off"

# Give disk a moment to flush
sleep 5

backup_ok=0
backup_target=""

if [[ "$BACKUP_TRIGGER" == "auto" ]]; then
  if ! command -v rsync >/dev/null 2>&1; then
    echo "[$(date +"%Y-%m-%d %H:%M:%S")] backup aborted: rsync is required for auto snapshots"
  else
    mkdir -p "$AUTO_SNAPSHOT_DIR"
    SNAPSHOT_DIR="$AUTO_SNAPSHOT_DIR/${WORLD_NAME}_${DATE}${BACKUP_SUFFIX}"
    PREVIOUS_SNAPSHOT="$(
      find "$AUTO_SNAPSHOT_DIR" -mindepth 1 -maxdepth 1 -type d -name "${WORLD_NAME}_*_auto*" \
        | sort \
        | tail -n 1
    )"

    if [[ -n "$PREVIOUS_SNAPSHOT" && -d "$PREVIOUS_SNAPSHOT" ]]; then
      if rsync -a --delete --link-dest="$PREVIOUS_SNAPSHOT/" "$WORLD_DIR"/ "$SNAPSHOT_DIR"/; then
        backup_ok=1
        backup_target="$SNAPSHOT_DIR"
      fi
    else
      if rsync -a --delete "$WORLD_DIR"/ "$SNAPSHOT_DIR"/; then
        backup_ok=1
        backup_target="$SNAPSHOT_DIR"
      fi
    fi
  fi
else
  BACKUP_ZIP="$BACKUP_DIR/${WORLD_NAME}_${DATE}${BACKUP_SUFFIX}.zip"
  if zip -r "$BACKUP_ZIP" "$WORLD_DIR"; then
    backup_ok=1
    backup_target="$BACKUP_ZIP"
  fi
fi

if [[ "$backup_ok" -eq 1 ]]; then
  mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "say Backup completed successfully! Saved to $backup_target"
else
  mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "say Backup failed! Check server logs."
fi

# Re-enable saving
mcrcon -H "$RCON_HOST" -P "$RCON_PORT" -p "$RCON_PASS" "save-on"
WORLD_SAVE_DISABLED="false"

if [[ "$backup_ok" -ne 1 ]]; then
  exit 1
fi
