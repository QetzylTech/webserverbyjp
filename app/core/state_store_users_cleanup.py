"""User/device and cleanup records for app state DB."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from app.core.state_store_core import _CLEANUP_CONFIG_KEY, _connect, _create_tables
from app.core import profiling


DbPath = str | Path
JsonDict = dict[str, object]


def _coerce_dict(raw: object) -> JsonDict | None:
    if not isinstance(raw, dict):
        return None
    return {str(key): value for key, value in raw.items()}


def upsert_user_record(db_path: DbPath, *, ip: object, timestamp: object, device_name: object) -> None:
    """Create or update one user-login registry row."""
    with profiling.timed("sqlite.users.upsert"):
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


def load_fallmap(db_path: DbPath) -> dict[str, str]:
    """Return IP -> device name mapping from SQLite."""
    with profiling.timed("sqlite.fallmap.load"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            rows = conn.execute(
                "SELECT ip, device_name FROM device_fallmap ORDER BY ip ASC"
            ).fetchall()
    mapping: dict[str, str] = {}
    for row in rows:
        ip = str(row["ip"] or "").strip()
        name = str(row["device_name"] or "").strip()
        if ip and name:
            mapping[ip] = name
    return mapping


def load_fallmap_rows(db_path: DbPath) -> list[JsonDict]:
    """Return device fallmap rows with owner metadata."""
    with profiling.timed("sqlite.fallmap.load_rows"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            rows = conn.execute(
                "SELECT ip, device_name, owner FROM device_fallmap ORDER BY ip ASC"
            ).fetchall()
    output: list[JsonDict] = []
    for row in rows:
        ip = str(row["ip"] or "").strip()
        name = str(row["device_name"] or "").strip()
        owner = str(row["owner"] or "").strip()
        if ip and name:
            output.append({"ip": ip, "device_name": name, "owner": owner})
    return output


def load_user_records(db_path: DbPath) -> list[JsonDict]:
    """Return user activity rows ordered by most recently updated first."""
    with profiling.timed("sqlite.users.load"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            rows = conn.execute(
                """
                SELECT ip, timestamp, device_name, updated_at
                FROM users
                ORDER BY updated_at DESC, ip ASC
                """
            ).fetchall()
    records: list[JsonDict] = []
    for row in rows:
        records.append(
            {
                "ip": str(row["ip"] or "").strip(),
                "timestamp": str(row["timestamp"] or "").strip(),
                "device_name": str(row["device_name"] or "").strip(),
                "updated_at": str(row["updated_at"] or "").strip(),
            }
        )
    return records


def replace_fallmap(db_path: DbPath, mapping: Mapping[object, object]) -> None:
    """Replace the device_fallmap table with the provided IP -> device name mapping."""
    rows = []
    for ip, name in (mapping or {}).items():
        ip_text = str(ip or "").strip()
        name_text = str(name or "").strip()
        if not ip_text or not name_text:
            continue
        rows.append({"ip": ip_text, "device_name": name_text, "owner": ""})
    replace_fallmap_rows(db_path, rows)


def replace_fallmap_rows(db_path: DbPath, rows: object) -> None:
    """Replace the device_fallmap table with full row metadata."""
    with profiling.timed("sqlite.fallmap.replace"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            conn.execute("DELETE FROM device_fallmap")
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                ip_text = str(row.get("ip", "") or "").strip()
                name_text = str(row.get("device_name", "") or row.get("name", "") or "").strip()
                owner_text = str(row.get("owner", "") or "").strip()
                if not ip_text or not name_text:
                    continue
                conn.execute(
                    """
                    INSERT INTO device_fallmap (ip, device_name, owner, updated_at)
                    VALUES (?, ?, ?, datetime('now'))
                    """,
                    (ip_text, name_text, owner_text),
                )
            conn.commit()


def load_cleanup_config(db_path: DbPath) -> JsonDict | None:
    """Load cleanup config document from SQLite."""
    with profiling.timed("sqlite.cleanup.load_config"):
        with _connect(db_path) as conn:
            _create_tables(conn)
            row = conn.execute(
                "SELECT json_text FROM cleanup_store WHERE key = ? LIMIT 1",
                (_CLEANUP_CONFIG_KEY,),
            ).fetchone()
    if row is None:
        return None
    try:
        payload = _coerce_dict(json.loads(row["json_text"]))
    except Exception:
        return None
    return payload


def save_cleanup_config(db_path: DbPath, payload: Mapping[str, object]) -> None:
    """Persist cleanup config document into SQLite."""
    text = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=True, sort_keys=True)
    with profiling.timed("sqlite.cleanup.save_config"):
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


def load_cleanup_history_runs(db_path: DbPath, *, limit: int = 500) -> list[JsonDict]:
    """Load cleanup history runs (oldest -> newest) with bounded length."""
    max_rows = max(1, int(limit))
    with profiling.timed("sqlite.cleanup.load_history"):
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
    runs: list[JsonDict] = []
    for row in rows:
        try:
            item = _coerce_dict(json.loads(row["run_json"]))
        except Exception:
            continue
        if item is not None:
            runs.append(item)
    return runs


def append_cleanup_history_run(db_path: DbPath, run_payload: Mapping[str, object], *, max_rows: int = 500) -> None:
    """Append one cleanup history run and trim older rows."""
    payload = dict(run_payload)
    with profiling.timed("sqlite.cleanup.append_history"):
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


def save_cleanup_history_runs(db_path: DbPath, runs: object, *, max_rows: int = 500) -> None:
    """Replace full cleanup history set with bounded normalized rows."""
    normalized: list[JsonDict] = []
    if isinstance(runs, list):
        for item in runs:
            coerced = _coerce_dict(item)
            if coerced is not None:
                normalized.append(coerced)
    normalized = normalized[-max(1, int(max_rows)) :]
    with profiling.timed("sqlite.cleanup.save_history"):
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
