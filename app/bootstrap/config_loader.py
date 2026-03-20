"""Typed config loader helpers for application composition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.web_config import WebConfig


_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppConfig:
    """Validated, typed application configuration for runtime/bootstrap use."""

    web_conf_path: Path
    raw_values: dict[str, str]
    service: str
    admin_password_hash: str
    require_password: bool
    backup_dir: Path
    minecraft_root_dir: Path
    doc_readme_url: str
    device_map_csv_path: Path
    display_tz_name: str
    maintenance_scope_backup_zip: bool
    maintenance_scope_stale_world_dir: bool
    maintenance_scope_old_world_zip: bool
    backup_interval_hours: float
    idle_zero_players_seconds: int
    idle_check_interval_seconds: int
    idle_check_interval_active_seconds: int
    idle_check_interval_off_seconds: int
    mc_query_interval_seconds: int
    metrics_collect_interval_seconds: int
    metrics_collect_interval_off_seconds: int
    metrics_idle_storage_refresh_seconds: float
    metrics_stream_heartbeat_seconds: int
    log_stream_heartbeat_seconds: int
    log_stream_event_buffer_size: int
    minecraft_log_text_limit: int
    backup_log_text_limit: int
    mcweb_log_text_limit: int
    mcweb_action_log_text_limit: int
    minecraft_journal_tail_lines: int
    minecraft_log_visible_lines: int
    home_page_active_ttl_seconds: int
    home_page_heartbeat_interval_ms: int
    file_page_cache_refresh_seconds: int
    file_page_active_ttl_seconds: int
    file_page_heartbeat_interval_ms: int
    crash_stop_grace_seconds: int
    backup_watch_interval_active_seconds: int
    backup_watch_interval_off_seconds: int
    backup_warning_ttl_seconds: float
    low_storage_available_threshold_percent: float
    storage_safety_check_interval_active_seconds: int
    storage_safety_check_interval_off_seconds: int
    operation_reconcile_interval_seconds: float
    operation_intent_stale_seconds: float
    operation_start_timeout_seconds: float
    operation_stop_timeout_seconds: float
    operation_restore_timeout_seconds: float
    service_status_cache_active_seconds: float
    service_status_cache_off_seconds: float
    service_status_command_timeout_seconds: float
    journal_load_timeout_seconds: float
    rcon_startup_journal_timeout_seconds: float
    slow_metrics_interval_active_seconds: float
    slow_metrics_interval_off_seconds: float
    log_fetcher_idle_sleep_seconds: float
    log_fetcher_idle_poll_seconds: float
    process_role: str
    port: int
    secret_key_value: str
    debug_app_host: str
    debug_app_port: int


def _cfg_bool(web_cfg: WebConfig, name: str, default: str = "false") -> bool:
    return web_cfg.get_str(name, default).strip().lower() in _TRUE_VALUES


def load_web_config(app_dir: Path, *, default_backup_dir: Path, default_minecraft_root: Path) -> AppConfig:
    """Load mcweb.env and return one validated typed config object."""
    web_conf_path = Path(app_dir) / "mcweb.env"
    web_cfg = WebConfig(web_conf_path, app_dir)
    values = dict(web_cfg.values)
    process_role = web_cfg.get_str("MCWEB_PROCESS_ROLE", "all").strip().lower() or "all"
    if process_role not in {"all", "web", "worker"}:
        process_role = "all"

    metrics_collect_interval_seconds = web_cfg.get_int("METRICS_COLLECT_INTERVAL_SECONDS", 1, minimum=1)
    idle_check_interval_seconds = web_cfg.get_int("IDLE_CHECK_INTERVAL_SECONDS", 5, minimum=1)
    idle_check_interval_active_seconds = web_cfg.get_int(
        "IDLE_CHECK_INTERVAL_ACTIVE_SECONDS",
        idle_check_interval_seconds,
        minimum=1,
    )
    backup_watch_interval_active_seconds = web_cfg.get_int("BACKUP_WATCH_INTERVAL_ACTIVE_SECONDS", 15, minimum=1)

    return AppConfig(
        web_conf_path=web_conf_path,
        raw_values=values,
        service=web_cfg.get_str("SERVICE", "minecraft"),
        admin_password_hash=web_cfg.get_str("MCWEB_ADMIN_PASSWORD_HASH", ""),
        require_password=_cfg_bool(web_cfg, "MCWEB_REQUIRE_PASSWORD", "true"),
        backup_dir=web_cfg.get_path("BACKUP_DIR", Path(default_backup_dir)),
        minecraft_root_dir=web_cfg.get_path("MINECRAFT_ROOT_DIR", Path(default_minecraft_root)),
        doc_readme_url=web_cfg.get_str("DOC_README_URL", "/doc/server_setup_doc.md"),
        device_map_csv_path=web_cfg.get_path(
            "DEVICE_MAP_CSV_PATH",
            app_dir / "data" / "marites.minecraft@gmail.com-devices-2026-02-26T04-37-44-487Z.csv",
        ),
        display_tz_name=web_cfg.get_str("DISPLAY_TZ", "Asia/Manila"),
        maintenance_scope_backup_zip=_cfg_bool(web_cfg, "MAINTENANCE_SCOPE_BACKUP_ZIP", "true"),
        maintenance_scope_stale_world_dir=_cfg_bool(web_cfg, "MAINTENANCE_SCOPE_STALE_WORLD_DIR", "true"),
        maintenance_scope_old_world_zip=_cfg_bool(web_cfg, "MAINTENANCE_SCOPE_OLD_WORLD_ZIP", "true"),
        backup_interval_hours=web_cfg.get_float("BACKUP_INTERVAL_HOURS", 3.0, minimum=1 / 60.0),
        idle_zero_players_seconds=web_cfg.get_int("IDLE_ZERO_PLAYERS_SECONDS", 180, minimum=10),
        idle_check_interval_seconds=idle_check_interval_seconds,
        idle_check_interval_active_seconds=idle_check_interval_active_seconds,
        idle_check_interval_off_seconds=web_cfg.get_int(
            "IDLE_CHECK_INTERVAL_OFF_SECONDS",
            max(idle_check_interval_active_seconds, 15),
            minimum=1,
        ),
        mc_query_interval_seconds=web_cfg.get_int("MC_QUERY_INTERVAL_SECONDS", 3, minimum=1),
        metrics_collect_interval_seconds=metrics_collect_interval_seconds,
        metrics_collect_interval_off_seconds=web_cfg.get_int(
            "METRICS_COLLECT_INTERVAL_OFF_SECONDS",
            max(metrics_collect_interval_seconds, 5),
            minimum=1,
        ),
        metrics_idle_storage_refresh_seconds=web_cfg.get_float(
            "METRICS_IDLE_STORAGE_REFRESH_SECONDS",
            15.0,
            minimum=1.0,
        ),
        metrics_stream_heartbeat_seconds=web_cfg.get_int("METRICS_STREAM_HEARTBEAT_SECONDS", 5, minimum=1),
        log_stream_heartbeat_seconds=web_cfg.get_int("LOG_STREAM_HEARTBEAT_SECONDS", 5, minimum=1),
        log_stream_event_buffer_size=web_cfg.get_int("LOG_STREAM_EVENT_BUFFER_SIZE", 800, minimum=50),
        minecraft_log_text_limit=web_cfg.get_int("MINECRAFT_LOG_TEXT_LIMIT", 1000, minimum=10),
        backup_log_text_limit=web_cfg.get_int("BACKUP_LOG_TEXT_LIMIT", 200, minimum=10),
        mcweb_log_text_limit=web_cfg.get_int("MCWEB_LOG_TEXT_LIMIT", 200, minimum=10),
        mcweb_action_log_text_limit=web_cfg.get_int("MCWEB_ACTION_LOG_TEXT_LIMIT", 200, minimum=10),
        minecraft_journal_tail_lines=web_cfg.get_int("MINECRAFT_JOURNAL_TAIL_LINES", 1000, minimum=10),
        minecraft_log_visible_lines=web_cfg.get_int("MINECRAFT_LOG_VISIBLE_LINES", 500, minimum=10),
        home_page_active_ttl_seconds=web_cfg.get_int("HOME_PAGE_ACTIVE_TTL_SECONDS", 30, minimum=1),
        home_page_heartbeat_interval_ms=web_cfg.get_int("HOME_PAGE_HEARTBEAT_INTERVAL_MS", 10000, minimum=1000),
        file_page_cache_refresh_seconds=web_cfg.get_int("FILE_PAGE_CACHE_REFRESH_SECONDS", 15, minimum=1),
        file_page_active_ttl_seconds=web_cfg.get_int("FILE_PAGE_ACTIVE_TTL_SECONDS", 30, minimum=1),
        file_page_heartbeat_interval_ms=web_cfg.get_int("FILE_PAGE_HEARTBEAT_INTERVAL_MS", 10000, minimum=1000),
        crash_stop_grace_seconds=web_cfg.get_int("CRASH_STOP_GRACE_SECONDS", 15, minimum=1),
        backup_watch_interval_active_seconds=backup_watch_interval_active_seconds,
        backup_watch_interval_off_seconds=web_cfg.get_int(
            "BACKUP_WATCH_INTERVAL_OFF_SECONDS",
            max(backup_watch_interval_active_seconds, 45),
            minimum=1,
        ),
        backup_warning_ttl_seconds=web_cfg.get_float("BACKUP_WARNING_TTL_SECONDS", 120.0, minimum=1.0),
        low_storage_available_threshold_percent=web_cfg.get_float(
            "LOW_STORAGE_AVAILABLE_THRESHOLD_PERCENT",
            10.0,
            minimum=0.1,
        ),
        storage_safety_check_interval_active_seconds=web_cfg.get_int(
            "STORAGE_SAFETY_CHECK_INTERVAL_ACTIVE_SECONDS",
            5,
            minimum=1,
        ),
        storage_safety_check_interval_off_seconds=web_cfg.get_int(
            "STORAGE_SAFETY_CHECK_INTERVAL_OFF_SECONDS",
            15,
            minimum=1,
        ),
        operation_reconcile_interval_seconds=web_cfg.get_float("OPERATION_RECONCILE_INTERVAL_SECONDS", 2.0, minimum=0.5),
        operation_intent_stale_seconds=web_cfg.get_float("OPERATION_INTENT_STALE_SECONDS", 15.0, minimum=1.0),
        operation_start_timeout_seconds=web_cfg.get_float("OPERATION_START_TIMEOUT_SECONDS", 180.0, minimum=5.0),
        operation_stop_timeout_seconds=web_cfg.get_float("OPERATION_STOP_TIMEOUT_SECONDS", 180.0, minimum=5.0),
        operation_restore_timeout_seconds=web_cfg.get_float("OPERATION_RESTORE_TIMEOUT_SECONDS", 7200.0, minimum=30.0),
        service_status_cache_active_seconds=web_cfg.get_float("SERVICE_STATUS_CACHE_ACTIVE_SECONDS", 1.0, minimum=0.0),
        service_status_cache_off_seconds=web_cfg.get_float("SERVICE_STATUS_CACHE_OFF_SECONDS", 5.0, minimum=0.0),
        service_status_command_timeout_seconds=web_cfg.get_float("SERVICE_STATUS_COMMAND_TIMEOUT_SECONDS", 3.0, minimum=0.5),
        journal_load_timeout_seconds=web_cfg.get_float("JOURNAL_LOAD_TIMEOUT_SECONDS", 4.0, minimum=0.5),
        rcon_startup_journal_timeout_seconds=web_cfg.get_float("RCON_STARTUP_JOURNAL_TIMEOUT_SECONDS", 4.0, minimum=0.5),
        slow_metrics_interval_active_seconds=web_cfg.get_float("SLOW_METRICS_INTERVAL_ACTIVE_SECONDS", 5.0, minimum=1.0),
        slow_metrics_interval_off_seconds=web_cfg.get_float("SLOW_METRICS_INTERVAL_OFF_SECONDS", 15.0, minimum=1.0),
        log_fetcher_idle_sleep_seconds=web_cfg.get_float("LOG_FETCHER_IDLE_SLEEP_SECONDS", 2.0, minimum=0.5),
        log_fetcher_idle_poll_seconds=web_cfg.get_float("LOG_FETCHER_IDLE_POLL_SECONDS", 15.0, minimum=1.0),
        process_role=process_role,
        port=web_cfg.get_int("PORT", 8080),
        secret_key_value=web_cfg.get_str("MCWEB_SECRET_KEY", ""),
        debug_app_host=web_cfg.get_str("DEBUG_APP_HOST", "127.0.0.1"),
        debug_app_port=web_cfg.get_int("DEBUG_APP_PORT", 8765, minimum=1),
    )
