"""XLSX-Export und Export-Historie."""
from __future__ import annotations

import os
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from config import Config
from database import get_pool
from deps import app_config, get_current_user
from routers.cats import load_config
from xlsx_export import ExportRow, build_bytes, suggest_filename

router = APIRouter(prefix="/api/exports", tags=["exports"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("", summary="Offene Zeilen eines Zeitraums als XLSX exportieren")
async def create_export(
    date_from: date = Query(...),
    date_to: date = Query(...),
    include_exported: bool = Query(
        default=False, description="Bereits exportierte Zeilen erneut mitnehmen"
    ),
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    cfg: Config = Depends(app_config),
) -> dict:
    if date_from > date_to:
        raise HTTPException(400, "Der Zeitraum ist verdreht: von liegt hinter bis.")

    user_id = int(user["sub"])
    cfg_row = await load_config(pool, user_id)
    if not cfg_row:
        raise HTTPException(400, "Bitte zuerst die CATS-Config ausfuellen.")

    condition = "" if include_exported else " AND export_id IS NULL"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, work_date, wbs_element, hours FROM cats_entry
            WHERE user_id = $1 AND work_date BETWEEN $2 AND $3{condition}
            ORDER BY work_date, wbs_element
            """,
            user_id, date_from, date_to,
        )

    if not rows:
        raise HTTPException(404, "Im gewaehlten Zeitraum gibt es keine Zeilen zum Export.")

    personnel_number = cfg_row["personnel_number"]
    payload = build_bytes([
        ExportRow(r["work_date"], personnel_number, r["wbs_element"], float(r["hours"]))
        for r in rows
    ])

    directory = os.path.join(cfg.data_dir, str(user_id), "exports")
    os.makedirs(directory, exist_ok=True)
    filename = suggest_filename(date_from, date_to)
    total_hours = round(sum(float(r["hours"]) for r in rows), 2)

    async with pool.acquire() as conn:
        async with conn.transaction():
            export_id = await conn.fetchval(
                """
                INSERT INTO export
                    (user_id, filename, stored_path, date_from, date_to, row_count, total_hours)
                VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
                """,
                user_id, filename, "", date_from, date_to, len(rows), total_hours,
            )
            # Export-ID im Dateinamen, damit sich Exporte desselben Zeitraums
            # nicht gegenseitig ueberschreiben.
            path = os.path.join(directory, f"{export_id}_{filename}")
            with open(path, "wb") as f:
                f.write(payload)
            await conn.execute(
                "UPDATE export SET stored_path = $1 WHERE id = $2", path, export_id
            )
            await conn.execute(
                "UPDATE cats_entry SET export_id = $1 WHERE id = ANY($2::bigint[])",
                export_id, [r["id"] for r in rows],
            )

    return {
        "export_id": export_id,
        "filename": filename,
        "row_count": len(rows),
        "total_hours": total_hours,
        "download_url": f"/api/exports/{export_id}/download",
    }


@router.get("", summary="Export-Historie")
async def list_exports(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, filename, date_from, date_to, row_count, total_hours, created_at
            FROM export WHERE user_id = $1 ORDER BY created_at DESC LIMIT 200
            """,
            int(user["sub"]),
        )
    return [
        {**dict(r), "total_hours": float(r["total_hours"]),
         "download_url": f"/api/exports/{r['id']}/download"}
        for r in rows
    ]


@router.get("/{export_id}/download", summary="Erzeugte XLSX herunterladen")
async def download_export(
    export_id: int,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> FileResponse:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT filename, stored_path FROM export WHERE id = $1 AND user_id = $2",
            export_id, int(user["sub"]),
        )
    if not row:
        raise HTTPException(404, "Export nicht gefunden.")
    if not os.path.exists(row["stored_path"]):
        raise HTTPException(410, "Die Datei ist auf dem Server nicht mehr vorhanden.")
    return FileResponse(
        row["stored_path"], media_type=XLSX_MEDIA_TYPE, filename=row["filename"]
    )


@router.delete("/{export_id}", summary="Export zuruecknehmen")
async def revoke_export(
    export_id: int,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Setzt die Zeilen wieder auf offen -- fuer den Fall, dass die Datei in SAP
    nicht angekommen ist. Die Datei selbst bleibt in der Historie liegen."""
    user_id = int(user["sub"])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM export WHERE id = $1 AND user_id = $2", export_id, user_id
        )
        if not row:
            raise HTTPException(404, "Export nicht gefunden.")
        status = await conn.execute(
            "UPDATE cats_entry SET export_id = NULL WHERE export_id = $1 AND user_id = $2",
            export_id, user_id,
        )
    # asyncpg liefert den Kommando-Status, z.B. "UPDATE 18".
    reopened = int(status.rsplit(" ", 1)[-1]) if status else 0
    return {"export_id": export_id, "reopened": reopened}
