"""File inventory records for app state DB."""

from __future__ import annotations

from pathlib import Path

from app.core.state_store_core import _connect, _create_tables
from app.core import profiling


DbPath = str | Path
FileRecord = dict[str, object]
NormalizedFileRow = tuple[str, float, int, str, str]


def _coerce_file_record(raw: object) -> FileRecord | None:
    if not isinstance(raw, dict):
        return None
    return {str(key): value for key, value in raw.items()}


def _to_int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            return int(value)
    except Exception:
        pass
    return default


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            return float(value)
    except Exception:
        pass
    return default


def replace_file_records_snapshot(db_path: DbPath, *, source_key: object, items: object) -> None:
    """Replace mutable file records and append immutable change history."""
    with profiling.timed("sqlite.file_records.replace_snapshot"):
        return _replace_file_records_snapshot_impl(db_path, source_key=source_key, items=items)


def load_file_records_snapshot(db_path: DbPath, *, source_key: object) -> list[FileRecord]:
    """Load the latest persisted file-record snapshot for one source key."""
    with profiling.timed("sqlite.file_records.load_snapshot"):
        return _load_file_records_snapshot_impl(db_path, source_key=source_key)


def _load_file_records_snapshot_impl(db_path: DbPath, *, source_key: object) -> list[FileRecord]:
    """Load the latest persisted file-record snapshot for one source key."""
    source = str(source_key or "").strip()
    if not source:
        return []
    with _connect(db_path) as conn:
        _create_tables(conn)
        rows = conn.execute(
            """
            SELECT name, mtime, size_bytes, modified_text, size_text
            FROM file_records
            WHERE source_key = ?
            ORDER BY mtime DESC, name ASC
            """,
            (source,),
        ).fetchall()
    items: list[FileRecord] = []
    for row in rows:
        items.append(
            {
                "name": str(row["name"] or ""),
                "mtime": float(row["mtime"] or 0),
                "size_bytes": int(row["size_bytes"] or 0),
                "modified": str(row["modified_text"] or ""),
                "size_text": str(row["size_text"] or ""),
            }
        )
    return items


def _replace_file_records_snapshot_impl(db_path: DbPath, *, source_key: object, items: object) -> None:
    """Replace mutable file records and append immutable change history."""
    source = str(source_key or "").strip()
    if not source:
        return

    normalized: list[NormalizedFileRow] = []
    if isinstance(items, list):
        for item in items:
            record = _coerce_file_record(item)
            if record is None:
                continue
            name = str(record.get("name", "") or "").strip()
            if not name:
                continue
            normalized.append(
                (
                    name,
                    _to_float(record.get("mtime", 0) or 0),
                    _to_int(record.get("size_bytes", 0) or 0),
                    str(record.get("modified", "") or ""),
                    str(record.get("size_text", "") or ""),
                )
            )

    with _connect(db_path) as conn:
        _create_tables(conn)
        previous_rows = conn.execute(
            """
            SELECT name, mtime, size_bytes, modified_text, size_text
            FROM file_records
            WHERE source_key = ?
            """,
            (source,),
        ).fetchall()
        previous = {
            str(row["name"]): (
                float(row["mtime"] or 0),
                int(row["size_bytes"] or 0),
                str(row["modified_text"] or ""),
                str(row["size_text"] or ""),
            )
            for row in previous_rows
        }
        current_names = {row[0] for row in normalized}
        removed_names = [name for name in previous.keys() if name not in current_names]

        history_events: list[tuple[str, str, float, int, str, str]] = []
        for row in normalized:
            name, mtime, size_bytes, modified_text, size_text = row
            prev = previous.get(name)
            if prev is None:
                history_events.append(("created", name, mtime, size_bytes, modified_text, size_text))
            elif (prev[0], prev[1], prev[2], prev[3]) != (mtime, size_bytes, modified_text, size_text):
                history_events.append(("updated", name, mtime, size_bytes, modified_text, size_text))
        for name in removed_names:
            prev = previous.get(name)
            if prev is None:
                continue
            history_events.append(("deleted", name, prev[0], prev[1], prev[2], prev[3]))

        for row in normalized:
            conn.execute(
                """
                INSERT INTO file_records (
                    source_key,
                    name,
                    mtime,
                    size_bytes,
                    modified_text,
                    size_text,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(source_key, name) DO UPDATE SET
                    mtime = excluded.mtime,
                    size_bytes = excluded.size_bytes,
                    modified_text = excluded.modified_text,
                    size_text = excluded.size_text,
                    updated_at = datetime('now')
                """,
                (source, row[0], row[1], row[2], row[3], row[4]),
            )
        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            params = [source] + [row[0] for row in normalized]
            conn.execute(
                f"DELETE FROM file_records WHERE source_key = ? AND name NOT IN ({placeholders})",
                params,
            )
        else:
            conn.execute("DELETE FROM file_records WHERE source_key = ?", (source,))
        for event in history_events:
            conn.execute(
                """
                INSERT INTO file_record_history (
                    source_key,
                    name,
                    event_type,
                    mtime,
                    size_bytes,
                    modified_text,
                    size_text,
                    observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (source, event[1], event[0], event[2], event[3], event[4], event[5]),
            )
        conn.commit()

