"""Expose Minecraft runtime helpers through one stable module."""

from app.services import log_stream_service as _log_stream
from app.services import rcon_probe_service as _rcon
from app.services import status_projection_service as _status

_LOG_STREAM_EXPORTS = (
    "_file_source_settings",
    "crash_stop_after_grace",
    "drain_buffered_log_lines",
    "decrement_log_stream_clients",
    "ensure_log_stream_fetcher_started",
    "flush_log_stream_batch",
    "get_log_source_text",
    "increment_log_stream_clients",
    "is_rcon_noise_line",
    "line_matches_crash_marker",
    "log_source_fetcher_loop",
    "log_source_settings",
    "normalize_log_source",
    "publish_log_stream_line",
    "schedule_crash_stop_if_needed",
)
_RCON_EXPORTS = (
    "clean_rcon_output",
    "get_players_online",
    "get_tick_rate",
    "is_rcon_enabled",
    "is_rcon_startup_ready",
    "parse_players_online",
    "probe_minecraft_runtime_metrics",
    "probe_tick_rate",
    "refresh_rcon_config",
    "run_mcrcon",
)
_STATUS_EXPORTS = (
    "get_service_status_class",
    "get_service_status_display",
)

for _name in _LOG_STREAM_EXPORTS:
    globals()[_name] = getattr(_log_stream, _name)
for _name in _RCON_EXPORTS:
    globals()[_name] = getattr(_rcon, _name)
for _name in _STATUS_EXPORTS:
    globals()[_name] = getattr(_status, _name)

del _name

__all__ = [
    *_LOG_STREAM_EXPORTS,
    *_RCON_EXPORTS,
    *_STATUS_EXPORTS,
]
