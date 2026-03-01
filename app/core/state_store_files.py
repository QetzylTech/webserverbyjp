"""File inventory records for app state DB."""

from __future__ import annotations

from app.core.state_store_core import _connect, _create_tables


def replace_file_records_snapshot(db_path, *, source_key, items):
    """Replace mutable file records and append immutable change history."""
    source = str(source_key or "").strip()
    if not source:
        return

    normalized = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if not name:
                continue
            normalized.append(
                (
                    name,
                    float(item.get("mtime", 0) or 0),
                    int(item.get("size_bytes", 0) or 0),
                    str(item.get("modified", "") or ""),
                    str(item.get("size_text", "") or ""),
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

        history_events = []
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
