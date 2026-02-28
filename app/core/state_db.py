"""SQLite-backed structured state storage helpers.

This module stores only structured dashboard records:
- users login registry
- device map entries
- maintenance cleanup config/history

Backups/snapshots and other file artifacts remain on disk.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


_CLEANUP_CONFIG_KEY = "cleanup_config"


def _connect(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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


def upsert_user_record(db_path, *, ip, timestamp, device_name):
    """Create or update one user-login registry row."""
    with _connect(db_path) as conn:
        _create_tables(conn)
        conn.execute(
            """
            INSERT INTO users (ip, timestamp, device_name, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(ip) DO UPDATE SET
                timestamp = excluded.timestamp,
                device_name = excluded.device_name,
                updated_at = datetime('now')
            """,
            (
                str(ip or "").strip(),
                str(timestamp or "").strip(),
                str(device_name or "").strip() or "unmapped-device",
            ),
        )
        conn.commit()


def load_fallmap(db_path):
    """Return IP -> device name mapping from SQLite."""
    with _connect(db_path) as conn:
        _create_tables(conn)
        rows = conn.execute(
            "SELECT ip, device_name FROM device_fallmap ORDER BY ip ASC"
        ).fetchall()
    mapping = {}
    for row in rows:
        ip = str(row["ip"] or "").strip()
        name = str(row["device_name"] or "").strip()
        if ip and name:
            mapping[ip] = name
    return mapping


def load_cleanup_config(db_path):
    """Load cleanup config document from SQLite."""
    with _connect(db_path) as conn:
        _create_tables(conn)
        row = conn.execute(
            "SELECT json_text FROM cleanup_store WHERE key = ? LIMIT 1",
            (_CLEANUP_CONFIG_KEY,),
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["json_text"])
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def save_cleanup_config(db_path, payload):
    """Persist cleanup config document into SQLite."""
    text = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=True, sort_keys=True)
    with _connect(db_path) as conn:
        _create_tables(conn)
        conn.execute(
            """
            INSERT INTO cleanup_store (key, json_text, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                json_text = excluded.json_text,
                updated_at = datetime('now')
            """,
            (_CLEANUP_CONFIG_KEY, text),
        )
        conn.commit()


def load_cleanup_history_runs(db_path, *, limit=500):
    """Load cleanup history runs (oldest -> newest) with bounded length."""
    max_rows = max(1, int(limit))
    with _connect(db_path) as conn:
        _create_tables(conn)
        rows = conn.execute(
            """
            SELECT run_json FROM (
                SELECT run_json, id
                FROM cleanup_history
                ORDER BY id DESC
                LIMIT ?
            ) AS tail
            ORDER BY id ASC
            """,
            (max_rows,),
        ).fetchall()
    runs = []
    for row in rows:
        try:
            item = json.loads(row["run_json"])
        except Exception:
            continue
        if isinstance(item, dict):
            runs.append(item)
    return runs


def append_cleanup_history_run(db_path, run_payload, *, max_rows=500):
    """Append one cleanup history run and trim older rows."""
    payload = run_payload if isinstance(run_payload, dict) else {}
    with _connect(db_path) as conn:
        _create_tables(conn)
        conn.execute(
            """
            INSERT INTO cleanup_history (at_text, run_json)
            VALUES (?, ?)
            """,
            (
                str(payload.get("at", "") or ""),
                json.dumps(payload, ensure_ascii=True, sort_keys=True),
            ),
        )
        keep = max(1, int(max_rows))
        conn.execute(
            """
            DELETE FROM cleanup_history
            WHERE id NOT IN (
                SELECT id
                FROM cleanup_history
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (keep,),
        )
        conn.commit()


def save_cleanup_history_runs(db_path, runs, *, max_rows=500):
    """Replace full cleanup history set with bounded normalized rows."""
    normalized = []
    if isinstance(runs, list):
        for item in runs:
            if isinstance(item, dict):
                normalized.append(item)
    normalized = normalized[-max(1, int(max_rows)) :]
    with _connect(db_path) as conn:
        _create_tables(conn)
        conn.execute("DELETE FROM cleanup_history")
        for item in normalized:
            conn.execute(
                "INSERT INTO cleanup_history (at_text, run_json) VALUES (?, ?)",
                (
                    str(item.get("at", "") or ""),
                    json.dumps(item, ensure_ascii=True, sort_keys=True),
                ),
            )
        conn.commit()
