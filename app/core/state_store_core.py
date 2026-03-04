"""Shared SQLite connection/schema helpers for app state store."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from app.core import profiling


_CLEANUP_CONFIG_KEY = "cleanup_config"
_SQLITE_CONNECT_TIMEOUT_SECONDS = 8.0
_SQLITE_BUSY_TIMEOUT_MS = 8000


def _connect(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    started = 0.0
    if profiling.ENABLED:
        started = time.perf_counter()
    conn = sqlite3.connect(str(path), timeout=_SQLITE_CONNECT_TIMEOUT_SECONDS)
    if profiling.ENABLED:
        profiling.record_duration("sqlite.connect", time.perf_counter() - started)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def _create_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            ip TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL DEFAULT '',
            device_name TEXT NOT NULL DEFAULT 'unmapped-device',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS device_fallmap (
            ip TEXT PRIMARY KEY,
            device_name TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cleanup_store (
            key TEXT PRIMARY KEY,
            json_text TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cleanup_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at_text TEXT NOT NULL DEFAULT '',
            run_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cleanup_history_id ON cleanup_history(id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS restore_name_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            backup_filename TEXT NOT NULL DEFAULT '',
            restore_source_name TEXT NOT NULL DEFAULT '',
            previous_world_name TEXT NOT NULL DEFAULT '',
            stored_world_name TEXT NOT NULL DEFAULT '',
            stored_id TEXT NOT NULL UNIQUE,
            active_world_name TEXT NOT NULL DEFAULT '',
            active_id TEXT NOT NULL UNIQUE,
            pre_restore_snapshot_name TEXT NOT NULL DEFAULT '',
            archived_old_world_name TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_restore_name_runs_created_at ON restore_name_runs(created_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS restore_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            job_id TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT '',
            backup_filename TEXT NOT NULL DEFAULT '',
            ok INTEGER NOT NULL DEFAULT 0,
            error_code TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            pre_restore_snapshot_name TEXT NOT NULL DEFAULT '',
            switched_from_world TEXT NOT NULL DEFAULT '',
            archived_old_world TEXT NOT NULL DEFAULT '',
            switched_to_world TEXT NOT NULL DEFAULT '',
            stored_restore_id TEXT NOT NULL DEFAULT '',
            active_restore_id TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_restore_runs_created_at ON restore_runs(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_restore_runs_job_id ON restore_runs(job_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS file_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            name TEXT NOT NULL,
            mtime REAL NOT NULL DEFAULT 0,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            modified_text TEXT NOT NULL DEFAULT '',
            size_text TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_key, name)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_records_source_key ON file_records(source_key)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS file_record_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL,
            name TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'updated',
            mtime REAL NOT NULL DEFAULT 0,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            modified_text TEXT NOT NULL DEFAULT '',
            size_text TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_record_history_source_key ON file_record_history(source_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_record_history_observed_at ON file_record_history(observed_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_topic_id ON events(topic, id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            op_id TEXT NOT NULL UNIQUE,
            op_type TEXT NOT NULL DEFAULT '',
            target TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'intent',
            checkpoint TEXT NOT NULL DEFAULT '',
            attempt INTEGER NOT NULL DEFAULT 1,
            intent_at TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            data_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_operations_op_type ON operations(op_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_operations_status ON operations(status)")

    # Backward-compatible migrations for existing databases.
    existing_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(operations)").fetchall()}
    if "idempotency_key" not in existing_cols:
        conn.execute("ALTER TABLE operations ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''")
    if "checkpoint" not in existing_cols:
        conn.execute("ALTER TABLE operations ADD COLUMN checkpoint TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_operations_idempotency_key ON operations(idempotency_key)")


def initialize_state_db(
    *,
    db_path,
    log_exception=None,
):
    """Create SQLite schema."""
    try:
        with _connect(db_path) as conn:
            _create_tables(conn)
            conn.commit()
        return True
    except Exception as exc:
        if callable(log_exception):
            try:
                log_exception("initialize_state_db", exc)
            except Exception:
                pass
        return False
