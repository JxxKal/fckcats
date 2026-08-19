"""Zugriff auf die verschluesselten Nutzdaten.

Buendelt Ver- und Entschluesselung an einer Stelle, damit die Router mit
gewoehnlichen Dictionaries arbeiten koennen. Summen und Filter ueber
verschluesselte Felder passieren zwangslaeufig im Speicher -- bei einigen
hundert Zeilen je Benutzer und Jahr faellt das nicht ins Gewicht.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg

import crypto


# ── CATS-Config ──────────────────────────────────────────────────────────────

async def load_config(pool: asyncpg.Pool, user_id: int, dek: bytes) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, version, payload_enc, created_at FROM cats_config "
            "WHERE user_id = $1 ORDER BY version DESC LIMIT 1",
            user_id,
        )
    if not row or not row["payload_enc"]:
        return None
    payload = crypto.decrypt_json(dek, bytes(row["payload_enc"]))
    return {"id": row["id"], "version": row["version"], **payload}


async def save_config(
    pool: asyncpg.Pool, user_id: int, dek: bytes, payload: dict
) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM cats_config WHERE user_id = $1",
                user_id,
            )
            await conn.execute(
                "INSERT INTO cats_config (user_id, version, payload_enc) VALUES ($1, $2, $3)",
                user_id, version, crypto.encrypt_json(dek, payload),
            )
    return version


async def update_config_payload(
    pool: asyncpg.Pool, config_id: int, dek: bytes, payload: dict
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE cats_config SET payload_enc = $1 WHERE id = $2",
            crypto.encrypt_json(dek, payload), config_id,
        )


# ── Validierte Tagesliste ────────────────────────────────────────────────────

async def load_workdays(
    pool: asyncpg.Pool, user_id: int, dek: bytes
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT work_date, payload_enc FROM workday "
            "WHERE user_id = $1 ORDER BY work_date",
            user_id,
        )
    out = []
    for r in rows:
        if not r["payload_enc"]:
            continue
        out.append({"work_date": r["work_date"],
                    **crypto.decrypt_json(dek, bytes(r["payload_enc"]))})
    return out


async def upsert_workdays(
    conn: asyncpg.Connection,
    user_id: int,
    dek: bytes,
    entries: list[tuple[date, float, str, str]],
    upload_id: int | None,
) -> None:
    for work_date, hours, source, reason in entries:
        payload = crypto.encrypt_json(
            dek, {"hours": hours, "source": source, "reason_text": reason}
        )
        await conn.execute(
            """
            INSERT INTO workday (user_id, work_date, payload_enc, upload_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, work_date) DO UPDATE SET
                payload_enc = EXCLUDED.payload_enc,
                upload_id   = EXCLUDED.upload_id
            """,
            user_id, work_date, payload, upload_id,
        )


# ── Zieltabelle ──────────────────────────────────────────────────────────────

async def load_entries(
    pool: asyncpg.Pool,
    user_id: int,
    dek: bytes,
    date_from: date | None = None,
    date_to: date | None = None,
    only_open: bool = False,
) -> list[dict]:
    clauses = ["user_id = $1"]
    params: list[Any] = [user_id]
    if date_from:
        params.append(date_from)
        clauses.append(f"work_date >= ${len(params)}")
    if date_to:
        params.append(date_to)
        clauses.append(f"work_date <= ${len(params)}")
    if only_open:
        clauses.append("exported_at IS NULL")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, work_date, payload_enc, export_id, exported_at
            FROM cats_entry WHERE {' AND '.join(clauses)} ORDER BY work_date, id
            """,
            *params,
        )
    out = []
    for r in rows:
        if not r["payload_enc"]:
            continue
        out.append({
            "id": r["id"],
            "work_date": r["work_date"],
            "export_id": r["export_id"],
            "exported_at": r["exported_at"],
            **crypto.decrypt_json(dek, bytes(r["payload_enc"])),
        })
    return out


async def insert_entries(
    conn: asyncpg.Connection,
    user_id: int,
    dek: bytes,
    rows: list[tuple[date, str, float]],
    run_id: int | None,
) -> None:
    for work_date, wbs, hours in rows:
        await conn.execute(
            """
            INSERT INTO cats_entry (user_id, work_date, payload_enc, wbs_hash, run_id)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id, work_date,
            crypto.encrypt_json(dek, {"wbs_element": wbs, "hours": hours}),
            crypto.blind_index(dek, wbs), run_id,
        )


# ── Uploads ──────────────────────────────────────────────────────────────────

async def load_upload(
    pool: asyncpg.Pool, upload_id: int, user_id: int, dek: bytes
) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, stored_path, payload_enc, uploaded_at, committed_at "
            "FROM uploads WHERE id = $1 AND user_id = $2",
            upload_id, user_id,
        )
    if not row:
        return None
    payload = crypto.decrypt_json(dek, bytes(row["payload_enc"])) if row["payload_enc"] else {}
    return {
        "id": row["id"],
        "stored_path": row["stored_path"],
        "uploaded_at": row["uploaded_at"],
        "committed_at": row["committed_at"],
        **payload,
    }


async def list_uploads(pool: asyncpg.Pool, user_id: int, dek: bytes) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, payload_enc, uploaded_at, committed_at FROM uploads "
            "WHERE user_id = $1 ORDER BY uploaded_at DESC LIMIT 100",
            user_id,
        )
    out = []
    for r in rows:
        payload = crypto.decrypt_json(dek, bytes(r["payload_enc"])) if r["payload_enc"] else {}
        out.append({
            "id": r["id"],
            "uploaded_at": r["uploaded_at"],
            "committed_at": r["committed_at"],
            "filename": payload.get("filename"),
            "period_month": payload.get("period_month"),
            "period_year": payload.get("period_year"),
        })
    return out


# ── Exporte ──────────────────────────────────────────────────────────────────

async def list_exports(pool: asyncpg.Pool, user_id: int, dek: bytes) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, stored_path, date_from, date_to, payload_enc, created_at "
            "FROM export WHERE user_id = $1 ORDER BY created_at DESC LIMIT 200",
            user_id,
        )
    out = []
    for r in rows:
        payload = crypto.decrypt_json(dek, bytes(r["payload_enc"])) if r["payload_enc"] else {}
        out.append({
            "id": r["id"],
            "date_from": r["date_from"],
            "date_to": r["date_to"],
            "created_at": r["created_at"],
            "filename": payload.get("filename", f"export-{r['id']}.xlsx"),
            "row_count": payload.get("row_count", 0),
            "total_hours": payload.get("total_hours", 0.0),
            "download_url": f"/api/exports/{r['id']}/download",
        })
    return out


# ── Aenderungsprotokoll ──────────────────────────────────────────────────────

async def archive_entries(
    conn: asyncpg.Connection, user_id: int, dek: bytes, days: list[date]
) -> None:
    """Sichert die zu ersetzenden Zeilen, bevor sie geloescht werden."""
    if not days:
        return
    rows = await conn.fetch(
        "SELECT work_date, payload_enc, export_id, exported_at FROM cats_entry "
        "WHERE user_id = $1 AND work_date = ANY($2::date[])",
        user_id, days,
    )
    by_day: dict[date, list[dict]] = {}
    for r in rows:
        if not r["payload_enc"]:
            continue
        by_day.setdefault(r["work_date"], []).append({
            **crypto.decrypt_json(dek, bytes(r["payload_enc"])),
            "export_id": r["export_id"],
            "exported_at": r["exported_at"].isoformat() if r["exported_at"] else None,
        })
    for day, payload in by_day.items():
        await conn.execute(
            "INSERT INTO entry_history (user_id, work_date, payload_enc, was_exported) "
            "VALUES ($1, $2, $3, $4)",
            user_id, day, crypto.encrypt_json(dek, payload),
            any(p["exported_at"] is not None for p in payload),
        )


async def load_history(pool: asyncpg.Pool, user_id: int, dek: bytes) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT work_date, payload_enc, was_exported, replaced_at FROM entry_history "
            "WHERE user_id = $1 ORDER BY replaced_at DESC LIMIT 500",
            user_id,
        )
    return [
        {
            "work_date": r["work_date"].isoformat(),
            "payload": crypto.decrypt_json(dek, bytes(r["payload_enc"])) if r["payload_enc"] else [],
            "was_exported": r["was_exported"],
            "replaced_at": r["replaced_at"],
        }
        for r in rows
    ]
