"""Einmalige Ueberfuehrung bestehender Klartextdaten in verschluesselte Form.

Wird beim Start ausgefuehrt und ist idempotent: sind die alten Spalten bereits
fort, passiert nichts. Verschluesselt wird mit dem Datenschluessel des jeweiligen
Benutzers, der dabei angelegt wird, falls er noch fehlt.
"""
from __future__ import annotations

import logging

import asyncpg

import crypto
from keys import ensure_user_key

log = logging.getLogger("fckcats.migrate")

# Tabelle -> (Klartextspalten, Bauplan des Blobs)
LEGACY_COLUMNS = {
    "cats_config":   ["personnel_number", "wbs_elements", "reason_rules"],
    "workday":       ["hours", "source", "reason_text"],
    "uploads":       ["filename", "personnel_number", "period_month",
                      "period_year", "parse_result"],
    "cats_entry":    ["wbs_element", "hours"],
    "export":        ["filename", "row_count", "total_hours"],
    "entry_history": ["payload"],
}

ADD_COLUMNS = """
ALTER TABLE cats_config   ADD COLUMN IF NOT EXISTS payload_enc BYTEA;
ALTER TABLE workday       ADD COLUMN IF NOT EXISTS payload_enc BYTEA;
ALTER TABLE uploads       ADD COLUMN IF NOT EXISTS payload_enc BYTEA;
ALTER TABLE cats_entry    ADD COLUMN IF NOT EXISTS payload_enc BYTEA;
ALTER TABLE cats_entry    ADD COLUMN IF NOT EXISTS wbs_hash    BYTEA;
ALTER TABLE export        ADD COLUMN IF NOT EXISTS payload_enc BYTEA;
ALTER TABLE entry_history ADD COLUMN IF NOT EXISTS payload_enc BYTEA;
"""


async def _has_column(conn: asyncpg.Connection, table: str, column: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = $1 AND column_name = $2",
        table, column,
    ))


async def _legacy_tables(conn: asyncpg.Connection) -> list[str]:
    """Tabellen, die noch mindestens eine Klartextspalte tragen."""
    out = []
    for table, columns in LEGACY_COLUMNS.items():
        for column in columns:
            if await _has_column(conn, table, column):
                out.append(table)
                break
    return out


async def run(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(ADD_COLUMNS)
        pending = await _legacy_tables(conn)
        if not pending:
            return
        log.warning("Klartextdaten gefunden in: %s -- wird verschluesselt.",
                    ", ".join(pending))

        user_ids = [r["id"] for r in await conn.fetch("SELECT id FROM users ORDER BY id")]
        deks: dict[int, bytes] = {}
        for user_id in user_ids:
            deks[user_id] = await ensure_user_key(conn, user_id)

        moved = 0

        if "cats_config" in pending:
            for r in await conn.fetch(
                "SELECT id, user_id, personnel_number, wbs_elements, reason_rules "
                "FROM cats_config WHERE payload_enc IS NULL"
            ):
                dek = deks.get(r["user_id"])
                if not dek:
                    continue
                await conn.execute(
                    "UPDATE cats_config SET payload_enc = $1 WHERE id = $2",
                    crypto.encrypt_json(dek, {
                        "personnel_number": r["personnel_number"],
                        "wbs_elements": r["wbs_elements"],
                        "reason_rules": r["reason_rules"],
                    }), r["id"])
                moved += 1

        if "workday" in pending:
            for r in await conn.fetch(
                "SELECT id, user_id, hours, source, reason_text "
                "FROM workday WHERE payload_enc IS NULL"
            ):
                dek = deks.get(r["user_id"])
                if not dek:
                    continue
                await conn.execute(
                    "UPDATE workday SET payload_enc = $1 WHERE id = $2",
                    crypto.encrypt_json(dek, {
                        "hours": float(r["hours"]),
                        "source": r["source"],
                        "reason_text": r["reason_text"],
                    }), r["id"])
                moved += 1

        if "uploads" in pending:
            for r in await conn.fetch(
                "SELECT id, user_id, filename, personnel_number, period_month, "
                "period_year, parse_result FROM uploads WHERE payload_enc IS NULL"
            ):
                dek = deks.get(r["user_id"])
                if not dek:
                    continue
                parsed = r["parse_result"] or {}
                await conn.execute(
                    "UPDATE uploads SET payload_enc = $1 WHERE id = $2",
                    crypto.encrypt_json(dek, {
                        "filename": r["filename"],
                        "personnel_number": r["personnel_number"],
                        "period_month": r["period_month"],
                        "period_year": r["period_year"],
                        "days": parsed.get("days", []),
                        "warnings": parsed.get("warnings", []),
                    }), r["id"])
                moved += 1

        if "cats_entry" in pending:
            for r in await conn.fetch(
                "SELECT id, user_id, wbs_element, hours FROM cats_entry "
                "WHERE payload_enc IS NULL"
            ):
                dek = deks.get(r["user_id"])
                if not dek:
                    continue
                await conn.execute(
                    "UPDATE cats_entry SET payload_enc = $1, wbs_hash = $2 WHERE id = $3",
                    crypto.encrypt_json(dek, {
                        "wbs_element": r["wbs_element"],
                        "hours": float(r["hours"]),
                    }),
                    crypto.blind_index(dek, r["wbs_element"]), r["id"])
                moved += 1

        if "export" in pending:
            for r in await conn.fetch(
                "SELECT id, user_id, filename, row_count, total_hours "
                "FROM export WHERE payload_enc IS NULL"
            ):
                dek = deks.get(r["user_id"])
                if not dek:
                    continue
                await conn.execute(
                    "UPDATE export SET payload_enc = $1 WHERE id = $2",
                    crypto.encrypt_json(dek, {
                        "filename": r["filename"],
                        "row_count": r["row_count"],
                        "total_hours": float(r["total_hours"]),
                    }), r["id"])
                moved += 1

        if "entry_history" in pending:
            for r in await conn.fetch(
                "SELECT id, user_id, payload FROM entry_history WHERE payload_enc IS NULL"
            ):
                dek = deks.get(r["user_id"])
                if not dek:
                    continue
                await conn.execute(
                    "UPDATE entry_history SET payload_enc = $1 WHERE id = $2",
                    crypto.encrypt_json(dek, r["payload"]), r["id"])
                moved += 1

        # Erst nach erfolgreicher Uebernahme faellt der Klartext weg. Die
        # Eindeutigkeit von cats_entry haengt danach am Blind Index.
        await conn.execute("""
            ALTER TABLE cats_entry DROP CONSTRAINT IF EXISTS
                cats_entry_user_id_work_date_wbs_element_key;
            ALTER TABLE cats_config   DROP COLUMN IF EXISTS personnel_number,
                                      DROP COLUMN IF EXISTS wbs_elements,
                                      DROP COLUMN IF EXISTS reason_rules;
            ALTER TABLE workday       DROP COLUMN IF EXISTS hours,
                                      DROP COLUMN IF EXISTS source,
                                      DROP COLUMN IF EXISTS reason_text;
            ALTER TABLE uploads       DROP COLUMN IF EXISTS filename,
                                      DROP COLUMN IF EXISTS personnel_number,
                                      DROP COLUMN IF EXISTS period_month,
                                      DROP COLUMN IF EXISTS period_year,
                                      DROP COLUMN IF EXISTS parse_result;
            ALTER TABLE cats_entry    DROP COLUMN IF EXISTS wbs_element,
                                      DROP COLUMN IF EXISTS hours;
            ALTER TABLE export        DROP COLUMN IF EXISTS filename,
                                      DROP COLUMN IF EXISTS row_count,
                                      DROP COLUMN IF EXISTS total_hours;
            ALTER TABLE entry_history DROP COLUMN IF EXISTS payload;
        """)
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint
                               WHERE conname = 'cats_entry_user_date_wbs_hash_key') THEN
                    ALTER TABLE cats_entry ADD CONSTRAINT cats_entry_user_date_wbs_hash_key
                        UNIQUE (user_id, work_date, wbs_hash);
                END IF;
            END $$;
        """)
        log.warning("%d Datensaetze verschluesselt, Klartextspalten entfernt.", moved)


async def encrypt_stored_files(pool: asyncpg.Pool, data_dir: str) -> None:
    """Verschluesselt bereits abgelegte PDFs und XLSX nachtraeglich."""
    import os

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, stored_path FROM uploads WHERE stored_path <> '' "
            "UNION ALL SELECT user_id, stored_path FROM export WHERE stored_path <> ''"
        )
        deks: dict[int, bytes] = {}
        touched = 0
        for r in rows:
            path = r["stored_path"]
            if not path or not os.path.exists(path):
                continue
            with open(path, "rb") as f:
                head = f.read(4)
            # Klartext erkennt man am Dateikopf: %PDF bzw. PK fuer XLSX.
            if head[:4] not in (b"%PDF",) and head[:2] != b"PK":
                continue
            if r["user_id"] not in deks:
                deks[r["user_id"]] = await ensure_user_key(conn, r["user_id"])
            with open(path, "rb") as f:
                data = f.read()
            crypto.encrypt_file(deks[r["user_id"]], data, path)
            touched += 1
        if touched:
            log.warning("%d abgelegte Dateien nachtraeglich verschluesselt.", touched)
