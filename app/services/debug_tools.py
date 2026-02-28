"""Debug-mode tools and server.properties editing helpers."""
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class DebugTools:
    """Encapsulates debug tooling so main.py stays lean."""
    def __init__(
        self,
        *,
        debug_enabled,
        debug_world_name,
        debug_motd,
        data_dir,
        app_dir,
        service,
        backup_script,
        backup_log_file,
        mcweb_action_log_file,
        backup_state_file,
        session_file,
        server_properties_candidates,
        debug_server_properties_keys,
        debug_server_properties_forced_values,
        debug_server_properties_int_keys,
        debug_server_properties_bool_keys,
        debug_server_properties_enums,
        debug_env_lock,
        debug_env_original_values,
        debug_env_overrides,
        backup_state,
        app,
        namespace,
        log_mcweb_log,
        log_mcweb_exception,
        log_debug_page_action,
        refresh_world_dir,
        refresh_rcon_config,
        invalidate_status_cache,
        set_service_status_intent,
        write_session_start_time,
        validate_sudo_password,
        record_successful_password_ip,
        graceful_stop_minecraft,
        clear_session_start_time,
        reset_backup_schedule_state,
        run_backup_script,
    ):
                # Dunder method __init__.
        self.DEBUG_ENABLED = debug_enabled
        self.DEBUG_WORLD_NAME = debug_world_name
        self.DEBUG_MOTD = debug_motd
        self.DATA_DIR = data_dir
        self.APP_DIR = app_dir
        self.SERVICE = service
        self.BACKUP_SCRIPT = backup_script
        self.BACKUP_LOG_FILE = backup_log_file
        self.MCWEB_ACTION_LOG_FILE = mcweb_action_log_file
        self.BACKUP_STATE_FILE = backup_state_file
        self.SESSION_FILE = session_file
        self.SERVER_PROPERTIES_CANDIDATES = server_properties_candidates
        self.DEBUG_SERVER_PROPERTIES_KEYS = debug_server_properties_keys
        self.DEBUG_SERVER_PROPERTIES_FORCED_VALUES = debug_server_properties_forced_values
        self.DEBUG_SERVER_PROPERTIES_INT_KEYS = debug_server_properties_int_keys
        self.DEBUG_SERVER_PROPERTIES_BOOL_KEYS = debug_server_properties_bool_keys
        self.DEBUG_SERVER_PROPERTIES_ENUMS = debug_server_properties_enums
        self.debug_env_lock = debug_env_lock
        self.debug_env_original_values = debug_env_original_values
        self.debug_env_overrides = debug_env_overrides
        self.backup_state = backup_state
        self.app = app
        self.namespace = namespace
        self.log_mcweb_log = log_mcweb_log
        self.log_mcweb_exception = log_mcweb_exception
        self.log_debug_page_action = log_debug_page_action
        self.refresh_world_dir = refresh_world_dir
        self.refresh_rcon_config = refresh_rcon_config
        self.invalidate_status_cache = invalidate_status_cache
        self.set_service_status_intent = set_service_status_intent
        self.write_session_start_time = write_session_start_time
        self.validate_sudo_password = validate_sudo_password
        self.record_successful_password_ip = record_successful_password_ip
        self.graceful_stop_minecraft = graceful_stop_minecraft
        self.clear_session_start_time = clear_session_start_time
        self.reset_backup_schedule_state = reset_backup_schedule_state
        self.run_backup_script = run_backup_script

    def detect_server_properties_path(self):
        """Runtime helper detect_server_properties_path."""
        for path in self.SERVER_PROPERTIES_CANDIDATES:
            if path.exists():
                return path
        return None

    def _debug_properties_state_path(self):
        return self.DATA_DIR / "properties" / "debug_properties.state"

    def _debug_properties_history_path(self):
        return self.DATA_DIR / "properties" / "debug_properties.history"

    def _timestamped_properties_backup_path(self):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return self.DATA_DIR / "properties" / f"server.properties.{stamp}.bak"

    def _record_debug_properties_history(self, event, *, props_path, backup_path=""):
        self._debug_properties_history_path().parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event": event,
            "props_path": str(props_path),
            "backup_path": str(backup_path) if backup_path else "",
        }
        line = json.dumps(row, ensure_ascii=True) + "\n"
        with self._debug_properties_history_path().open("a", encoding="utf-8") as fh:
            fh.write(line)

    def _read_debug_properties_state(self):
        path = self._debug_properties_state_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def _write_debug_properties_state(self, payload):
        self._debug_properties_state_path().parent.mkdir(parents=True, exist_ok=True)
        path = self._debug_properties_state_path()
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def _latest_server_properties_backup(self):
        backups = sorted((self.DATA_DIR / "properties").glob("server.properties.*.bak"))
        if not backups:
            return None
        return backups[-1]

    def _is_debug_world_properties_text(self, text):
        kv = self.parse_server_properties_kv(text)
        return kv.get("level-name", "").strip() == self.DEBUG_WORLD_NAME

    def _atomic_replace_file_from_source(self, source, destination):
        tmp = destination.with_name(f"{destination.name}.tmp")
        shutil.copyfile(source, tmp)
        os.replace(tmp, destination)

    def _atomic_write_text(self, destination, text):
        tmp = destination.with_name(f"{destination.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, destination)

    def update_property_text(self, original_text, key, value):
        """Runtime helper update_property_text."""
        lines = original_text.splitlines()
        target = f"{key}="
        found = False
        out = []
        for line in lines:
            if line.startswith(target):
                out.append(f"{target}{value}")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"{target}{value}")
        return "\n".join(out) + "\n"

    def prepare_debug_server_properties_bootup(self):
        """Runtime helper prepare_debug_server_properties_bootup."""
        props = self.detect_server_properties_path()
        if props is None:
            if self.DEBUG_ENABLED:
                self.log_mcweb_log("debug-boot", rejection_message="server.properties not found; debug provisioning skipped.")
            return

        try:
            text = props.read_text(encoding="utf-8", errors="ignore")
            active_is_debug = self._is_debug_world_properties_text(text)

            if self.DEBUG_ENABLED:
                if active_is_debug:
                    self.log_debug_page_action("debug-boot", command=f"kept active debug server.properties {props}")
                    return
                backup_path = self._timestamped_properties_backup_path()
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(props, backup_path)
                self._write_debug_properties_state({
                    "last_backup": str(backup_path),
                    "props_path": str(props),
                    "saved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                })
                self._record_debug_properties_history("snapshot_for_debug", props_path=props, backup_path=backup_path)
                text = self.update_property_text(text, "level-name", self.DEBUG_WORLD_NAME)
                text = self.update_property_text(text, "motd", self.DEBUG_MOTD)
                self._atomic_write_text(props, text)
                self.refresh_world_dir()
                self.log_debug_page_action("debug-boot", command=f"prepared {props} from {backup_path}")
                return

            if not active_is_debug:
                return
            state = self._read_debug_properties_state()
            restore_path = Path(state.get("last_backup", "")) if state.get("last_backup") else None
            if restore_path is None or not restore_path.exists():
                restore_path = self._latest_server_properties_backup()
            if restore_path is None or not restore_path.exists():
                self.log_mcweb_log(
                    "debug-boot-restore",
                    rejection_message="active debug server.properties detected but no backup found in data dir.",
                )
                return
            self._atomic_replace_file_from_source(restore_path, props)
            self._record_debug_properties_history("restore_after_debug", props_path=props, backup_path=restore_path)
            self.refresh_world_dir()
            self.log_mcweb_log("debug-boot-restore", command=f"restored {props} from {restore_path}")
        except OSError as exc:
            self.log_mcweb_exception("debug_boot_server_properties", exc)

    def parse_server_properties_kv(self, text):
        """Runtime helper parse_server_properties_kv."""
        kv = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            kv[key.strip()] = value.strip()
        return kv

    def typed_server_property_value(self, key, raw_value):
        """Runtime helper typed_server_property_value."""
        value = str(raw_value if raw_value is not None else "").strip()
        if key in self.DEBUG_SERVER_PROPERTIES_BOOL_KEYS:
            lowered = value.lower()
            if lowered in {"1", "true", "yes", "on"}:
                return "true"
            if lowered in {"0", "false", "no", "off"}:
                return "false"
            raise ValueError("must be true or false")
        if key in self.DEBUG_SERVER_PROPERTIES_INT_KEYS:
            if value == "":
                raise ValueError("must be an integer")
            try:
                return str(int(value))
            except ValueError as exc:
                raise ValueError("must be an integer") from exc
        enum_values = self.DEBUG_SERVER_PROPERTIES_ENUMS.get(key)
        if enum_values is not None:
            lowered = value.lower()
            if lowered not in enum_values:
                raise ValueError(f"must be one of: {', '.join(enum_values)}")
            return lowered
        return value

    def rewrite_server_properties_text(self, original_text, updated_values):
        """Runtime helper rewrite_server_properties_text."""
        lines = original_text.splitlines()
        seen = set()
        out = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in raw:
                out.append(raw)
                continue
            key, _ = raw.split("=", 1)
            key = key.strip()
            if key in updated_values:
                out.append(f"{key}={updated_values[key]}")
                seen.add(key)
            else:
                out.append(raw)
        for key in self.DEBUG_SERVER_PROPERTIES_KEYS:
            if key in updated_values and key not in seen:
                out.append(f"{key}={updated_values[key]}")
        text = "\n".join(out)
        if not text.endswith("\n"):
            text += "\n"
        return text

    def get_debug_server_properties_rows(self):
        """Runtime helper get_debug_server_properties_rows."""
        props = self.detect_server_properties_path()
        if props is None:
            return {"ok": False, "message": "server.properties not found."}
        try:
            text = props.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return {"ok": False, "message": "Failed to read server.properties."}
        kv = self.parse_server_properties_kv(text)
        rows = []
        for key in self.DEBUG_SERVER_PROPERTIES_KEYS:
            forced = key in self.DEBUG_SERVER_PROPERTIES_FORCED_VALUES
            current = kv.get(key, "")
            value = self.DEBUG_SERVER_PROPERTIES_FORCED_VALUES[key] if forced else current
            value_type = "string"
            if key in self.DEBUG_SERVER_PROPERTIES_BOOL_KEYS:
                value_type = "bool"
            elif key in self.DEBUG_SERVER_PROPERTIES_INT_KEYS:
                value_type = "int"
            elif key in self.DEBUG_SERVER_PROPERTIES_ENUMS:
                value_type = "enum"
            rows.append({
                "key": key,
                "value": value,
                "original": current,
                "type": value_type,
                "options": list(self.DEBUG_SERVER_PROPERTIES_ENUMS.get(key, ())),
                "editable": not forced,
                "forced": forced,
            })
        return {"ok": True, "path": str(props), "rows": rows}

    def set_debug_server_properties_values(self, values):
        """Runtime helper set_debug_server_properties_values."""
        props = self.detect_server_properties_path()
        if props is None:
            return {"ok": False, "message": "server.properties not found."}
        try:
            original_text = props.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return {"ok": False, "message": "Failed to read server.properties."}
        kv = self.parse_server_properties_kv(original_text)
        updated = {}
        errors = []
        for key in self.DEBUG_SERVER_PROPERTIES_KEYS:
            if key in self.DEBUG_SERVER_PROPERTIES_FORCED_VALUES:
                updated[key] = self.DEBUG_SERVER_PROPERTIES_FORCED_VALUES[key]
                continue
            raw = values.get(key, kv.get(key, ""))
            try:
                updated[key] = self.typed_server_property_value(key, raw)
            except ValueError as exc:
                errors.append(f"{key}: {exc}")
        if errors:
            return {"ok": False, "message": "Some server.properties values are invalid.", "errors": errors}
        try:
            next_text = self.rewrite_server_properties_text(original_text, updated)
            props.write_text(next_text, encoding="utf-8")
            self.refresh_world_dir()
            return {"ok": True, "path": str(props)}
        except OSError:
            return {"ok": False, "message": "Failed to write server.properties."}

    def debug_explorer_roots(self):
        """Runtime helper debug_explorer_roots."""
        return {"minecraft": Path("/opt/Minecraft")}

    def resolve_debug_explorer_target(self, root_key, rel_path):
        """Runtime helper resolve_debug_explorer_target."""
        roots = self.debug_explorer_roots()
        root = roots.get((root_key or "").strip())
        if root is None:
            return None, None, "Invalid root."
        try:
            root_resolved = Path(root).resolve()
        except OSError:
            return None, None, "Root path unavailable."
        candidate_rel = (rel_path or "").strip().replace("\\", "/")
        try:
            target = (root_resolved / candidate_rel).resolve() if candidate_rel else root_resolved
        except OSError:
            return None, None, "Path unavailable."
        try:
            if os.path.commonpath([str(root_resolved), str(target)]) != str(root_resolved):
                return None, None, "Path escapes allowed root."
        except ValueError:
            return None, None, "Path invalid."
        return root_resolved, target, ""

    def debug_explorer_list(self, root_key, rel_path=""):
        """Runtime helper debug_explorer_list."""
        root_resolved, target, err = self.resolve_debug_explorer_target(root_key, rel_path)
        if err:
            return {"ok": False, "message": err}
        if not target.exists():
            return {"ok": False, "message": "Directory not found."}
        if not target.is_dir():
            return {"ok": False, "message": "Target is not a directory."}
        entries = []
        try:
            for child in target.iterdir():
                kind = "dir" if child.is_dir() else "file"
                size = 0
                if kind == "file":
                    try:
                        size = child.stat().st_size
                    except OSError:
                        size = 0
                try:
                    rel = child.relative_to(root_resolved).as_posix()
                except ValueError:
                    continue
                entries.append({"name": child.name, "rel_path": rel, "kind": kind, "size": size})
        except OSError:
            return {"ok": False, "message": "Unable to list directory."}
        entries.sort(key=lambda item: (item["kind"] != "dir", item["name"].lower()))
        if len(entries) > 1000:
            entries = entries[:1000]
        current_rel = target.relative_to(root_resolved).as_posix() if target != root_resolved else ""
        return {
            "ok": True,
            "root_key": root_key,
            "root_path": str(root_resolved),
            "current_rel_path": current_rel,
            "entries": entries,
        }

    def log_mcweb_boot_diagnostics(self):
        """Runtime helper log_mcweb_boot_diagnostics."""
        try:
            server_props = self.detect_server_properties_path()
            _, rcon_port, rcon_enabled = self.refresh_rcon_config()
            details = (
                f"service={self.SERVICE}; "
                f"backup_script={self.BACKUP_SCRIPT} exists={self.BACKUP_SCRIPT.exists()}; "
                f"backup_log={self.BACKUP_LOG_FILE} exists={self.BACKUP_LOG_FILE.exists()}; "
                f"mcweb_action_log={self.MCWEB_ACTION_LOG_FILE}; "
                f"state_file={self.BACKUP_STATE_FILE} exists={self.BACKUP_STATE_FILE.exists()}; "
                f"session_file={self.SESSION_FILE} exists={self.SESSION_FILE.exists()}; "
                f"server_properties={(server_props if server_props else 'missing')}; "
                f"rcon_enabled={rcon_enabled}; rcon_port={rcon_port}"
            )
            self.log_mcweb_log("boot", command=details)
        except Exception as exc:
            self.log_mcweb_exception("boot_diagnostics", exc)

    def normalize_debug_value(self, raw):
        """Runtime helper normalize_debug_value."""
        return str(raw if raw is not None else "").strip()

    def resolve_debug_typed_value(self, name, raw_value):
        """Runtime helper resolve_debug_typed_value."""
        current = self.namespace.get(name)
        text = self.normalize_debug_value(raw_value)
        if isinstance(current, Path):
            if not text:
                return current
            candidate = Path(text)
            if candidate.is_absolute():
                return candidate
            return self.APP_DIR / candidate
        if isinstance(current, bool):
            return text.lower() in {"1", "true", "yes", "on"}
        if isinstance(current, int) and not isinstance(current, bool):
            return int(text)
        if isinstance(current, float):
            return float(text)
        return text

    def apply_single_debug_override(self, key, raw_value):
        """Runtime helper apply_single_debug_override."""
        value = self.normalize_debug_value(raw_value)
        with self.debug_env_lock:
            self.debug_env_overrides[key] = value
        if key == "MCWEB_SECRET_KEY":
            self.app.config["SECRET_KEY"] = value or self.app.config["SECRET_KEY"]
            return
        if key not in self.namespace:
            return
        typed = self.resolve_debug_typed_value(key, value)
        self.namespace[key] = typed
        state = self.namespace.get("STATE")
        if state is not None:
            try:
                state[key] = typed
            except KeyError:
                pass

    def reset_single_debug_override(self, key):
        """Runtime helper reset_single_debug_override."""
        original = self.debug_env_original_values.get(key, "")
        self.apply_single_debug_override(key, original)

    def reset_all_debug_overrides(self):
        """Runtime helper reset_all_debug_overrides."""
        with self.debug_env_lock:
            keys = list(self.debug_env_overrides.keys())
        for key in keys:
            self.reset_single_debug_override(key)
        with self.debug_env_lock:
            self.debug_env_overrides.clear()

    def apply_debug_env_overrides(self, values):
        """Runtime helper apply_debug_env_overrides."""
        errors = []
        for key, raw in values.items():
            try:
                self.apply_single_debug_override(key, raw)
            except (TypeError, ValueError) as exc:
                errors.append(f"{key}: {exc}")
        return errors

    def get_debug_env_rows(self):
        """Runtime helper get_debug_env_rows."""
        with self.debug_env_lock:
            overrides = dict(self.debug_env_overrides)
        rows = []
        for key, original in self.debug_env_original_values.items():
            effective = overrides.get(key, original)
            rows.append({
                "key": key,
                "value": effective,
                "original": original,
                "overridden": key in overrides and overrides.get(key) != original,
            })
        return rows

    def debug_start_service(self):
        """Runtime helper debug_start_service."""
        self.set_service_status_intent("starting")
        self.invalidate_status_cache()
        if self.write_session_start_time() is None:
            return False

        service_name = self.SERVICE

        def worker():
            try:
                result = subprocess.run(
                    ["sudo", "systemctl", "start", "--no-block", service_name],
                    capture_output=True,
                    text=True,
                    timeout=12,
                )
            except subprocess.TimeoutExpired:
                self.set_service_status_intent(None)
                self.invalidate_status_cache()
                self.log_debug_page_action(
                    "debug-start-worker",
                    rejection_message="Failed to start service: timed out issuing non-blocking start.",
                )
                return
            if result.returncode != 0:
                self.set_service_status_intent(None)
                self.invalidate_status_cache()
                detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
                message = "Failed to start service."
                if detail:
                    message = f"Failed to start service: {detail[:400]}"
                self.log_debug_page_action("debug-start-worker", rejection_message=message)
                return
            self.invalidate_status_cache()

        threading.Thread(target=worker, daemon=True).start()
        return True

    def debug_stop_service(self, sudo_password):
        """Runtime helper debug_stop_service."""
        if not self.validate_sudo_password(sudo_password):
            return False, "Password incorrect."
        self.record_successful_password_ip()
        self.set_service_status_intent("shutting")
        self.graceful_stop_minecraft()
        self.clear_session_start_time()
        self.reset_backup_schedule_state()
        return True, ""

    def debug_run_backup(self, trigger="manual"):
        """Runtime helper debug_run_backup."""
        return self.run_backup_script(trigger=trigger)

    def debug_schedule_backup(self, minutes, trigger="manual"):
        """Runtime helper debug_schedule_backup."""
        try:
            delay_minutes = int(minutes)
        except (TypeError, ValueError):
            return False, "Minutes must be a whole number."
        if delay_minutes <= 0:
            return False, "Minutes must be greater than zero."

        def worker():
            """Runtime helper worker."""
            time.sleep(delay_minutes * 60)
            ok = self.run_backup_script(trigger=trigger)
            if ok:
                self.log_debug_page_action("debug-backup-scheduled", command=f"ran trigger={trigger} after={delay_minutes}m")
            else:
                detail = ""
                with self.backup_state.lock:
                    detail = self.backup_state.last_error
                message = detail or "Scheduled backup failed."
                self.log_debug_page_action(
                    "debug-backup-scheduled",
                    command=f"ran trigger={trigger} after={delay_minutes}m",
                    rejection_message=message,
                )

        threading.Thread(target=worker, daemon=True).start()
        return True, ""
