"""Restore-related records for app state DB."""

from __future__ import annotations

from app.core.state_store_core import _connect, _create_tables


def restore_id_exists(db_path, restore_id):
    """Return True when the restore ID already exists in stored/active history."""
    code = str(restore_id or "").strip()
    if not code:
        return False
    with _connect(db_path) as conn:
        _create_tables(conn)
        row = conn.execute(
            """
            SELECT 1
            FROM restore_name_runs
            WHERE stored_id = ? OR active_id = ?
            LIMIT 1
            """,
            (code, code),
        ).fetchone()
    return row is not None


def append_restore_name_run(db_path, payload):
    """Append one restore naming run record."""
    item = payload if isinstance(payload, dict) else {}
    with _connect(db_path) as conn:
        _create_tables(conn)
        conn.execute(
            """
            INSERT INTO restore_name_runs (
                backup_filename,
                restore_source_name,
                previous_world_name,
                stored_world_name,
                stored_id,
                active_world_name,
                active_id,
                pre_restore_snapshot_name,
                archived_old_world_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(item.get("backup_filename", "") or ""),
                str(item.get("restore_source_name", "") or ""),
                str(item.get("previous_world_name", "") or ""),
                str(item.get("stored_world_name", "") or ""),
                str(item.get("stored_id", "") or ""),
                str(item.get("active_world_name", "") or ""),
                str(item.get("active_id", "") or ""),
                str(item.get("pre_restore_snapshot_name", "") or ""),
                str(item.get("archived_old_world_name", "") or ""),
            ),
        )
        conn.commit()


def append_restore_run(db_path, payload):
    """Append one restore run status record (success/failure)."""
    item = payload if isinstance(payload, dict) else {}
    with _connect(db_path) as conn:
        _create_tables(conn)
        conn.execute(
            """
            INSERT INTO restore_runs (
                job_id,
                mode,
                backup_filename,
                ok,
                error_code,
                message,
                pre_restore_snapshot_name,
                switched_from_world,
                archived_old_world,
                switched_to_world,
                stored_restore_id,
                active_restore_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(item.get("job_id", "") or ""),
                str(item.get("mode", "") or ""),
                str(item.get("backup_filename", "") or ""),
                1 if bool(item.get("ok")) else 0,
                str(item.get("error_code", "") or ""),
                str(item.get("message", "") or ""),
                str(item.get("pre_restore_snapshot_name", "") or ""),
                str(item.get("switched_from_world", "") or ""),
                str(item.get("archived_old_world", "") or ""),
                str(item.get("switched_to_world", "") or ""),
                str(item.get("stored_restore_id", "") or ""),
                str(item.get("active_restore_id", "") or ""),
            ),
        )
        conn.commit()


def restore_backup_records_match(
    db_path,
    *,
    backup_filename,
    pre_restore_snapshot_name,
    stored_restore_id="",
    active_restore_id="",
):
    """Return True when restore naming records match the backup restore run."""
    backup_name = str(backup_filename or "").strip()
    snapshot_name = str(pre_restore_snapshot_name or "").strip()
    if not backup_name or not snapshot_name:
        return False

    stored_id = str(stored_restore_id or "").strip()
    active_id = str(active_restore_id or "").strip()
    if stored_id.lower().startswith("gx"):
        stored_id = stored_id[2:]
    if active_id.lower().startswith("rx"):
        active_id = active_id[2:]

    with _connect(db_path) as conn:
        _create_tables(conn)
        row = conn.execute(
            """
            SELECT stored_id, active_id
            FROM restore_name_runs
            WHERE backup_filename = ?
              AND pre_restore_snapshot_name = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (backup_name, snapshot_name),
        ).fetchone()
    if row is None:
        return False
    if stored_id and str(row["stored_id"] or "").strip() != stored_id:
        return False
    if active_id and str(row["active_id"] or "").strip() != active_id:
        return False
    return True
