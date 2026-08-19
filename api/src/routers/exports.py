"""XLSX-Export und Export-Historie."""
from __future__ import annotations

import os
import random
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

import crypto
import store
from config import Config
from database import get_pool
from deps import app_config, get_current_user
from distribution import plan_range_best_of
from keys import get_dek
from routers.cats import load_config, weights_as_fractions
from storage_mode import storage_mode_of
from xlsx_export import ExportRow, build_bytes, suggest_filename

router = APIRouter(prefix="/api/exports", tags=["exports"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _remove_file(path: str) -> None:
    """Loescht die erzeugte Datei. Ein fehlendes File ist kein Fehler."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        # Der Datenbankeintrag ist schon weg; ein Dateirest darf den Aufruf
        # nicht scheitern lassen.
        pass


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
    dek: bytes = Depends(get_dek),
) -> dict:
    if date_from > date_to:
        raise HTTPException(400, "Der Zeitraum ist verdreht: von liegt hinter bis.")

    user_id = int(user["sub"])
    if await storage_mode_of(pool, user_id) != "persistent":
        raise HTTPException(
            409,
            "Für diesen Workspace ist die Speicherung abgeschaltet. "
            "Nutze den Direktexport aus der Vorschau.",
        )

    cfg_row = await load_config(pool, user_id, dek)
    if not cfg_row:
        raise HTTPException(400, "Bitte zuerst die CATS-Config ausfuellen.")

    rows = await store.load_entries(
        pool, user_id, dek, date_from, date_to, only_open=not include_exported
    )
    if not rows:
        raise HTTPException(404, "Im gewaehlten Zeitraum gibt es keine Zeilen zum Export.")

    personnel_number = cfg_row.get("personnel_number", "")
    payload = build_bytes([
        ExportRow(r["work_date"], personnel_number, r["wbs_element"], float(r["hours"]))
        for r in rows
    ])

    directory = os.path.join(cfg.data_dir, str(user_id), "exports")
    filename = suggest_filename(date_from, date_to)
    total_hours = round(sum(float(r["hours"]) for r in rows), 2)

    async with pool.acquire() as conn:
        async with conn.transaction():
            export_id = await conn.fetchval(
                """
                INSERT INTO export (user_id, stored_path, date_from, date_to, payload_enc)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
                """,
                user_id, "", date_from, date_to,
                crypto.encrypt_json(dek, {
                    "filename": filename,
                    "row_count": len(rows),
                    "total_hours": total_hours,
                }),
            )
            # Export-ID im Dateinamen, damit sich Exporte desselben Zeitraums
            # nicht gegenseitig ueberschreiben.
            path = os.path.join(directory, f"{export_id}_{filename}.enc")
            crypto.encrypt_file(dek, payload, path)
            await conn.execute(
                "UPDATE export SET stored_path = $1 WHERE id = $2", path, export_id
            )
            await conn.execute(
                "UPDATE cats_entry SET export_id = $1, exported_at = now() "
                "WHERE id = ANY($2::bigint[])",
                export_id, [r["id"] for r in rows],
            )

    return {
        "export_id": export_id,
        "filename": filename,
        "row_count": len(rows),
        "total_hours": total_hours,
        "download_url": f"/api/exports/{export_id}/download",
    }


class DirectDay(BaseModel):
    work_date: date
    hours: float = Field(gt=0, le=24)


class DirectExportRequest(BaseModel):
    """Verteilung und XLSX ohne jede Speicherung.

    Der Client schickt die in der Vorschau bestaetigten Tage; der Server
    verteilt sie und liefert die Datei zurueck. Es bleibt nichts liegen.
    """
    days: list[DirectDay]
    seed: int | None = None


@router.post("/direct", summary="XLSX erzeugen, ohne etwas zu speichern")
async def direct_export(
    body: DirectExportRequest,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    dek: bytes = Depends(get_dek),
) -> Response:
    if not body.days:
        raise HTTPException(400, "Es wurden keine Tage uebergeben.")

    user_id = int(user["sub"])
    cfg_row = await load_config(pool, user_id, dek)
    if not cfg_row:
        raise HTTPException(400, "Bitte zuerst die CATS-Config ausfuellen.")

    weights = weights_as_fractions(cfg_row)
    if not weights:
        raise HTTPException(400, "Die CATS-Config enthaelt keine WBS-Elemente.")

    days = sorted(((d.work_date, round(d.hours, 2)) for d in body.days))
    seed = body.seed if body.seed is not None else random.randrange(1, 2 ** 31 - 200)
    allocations, _, _ = plan_range_best_of(days, weights, seed)

    personnel_number = cfg_row.get("personnel_number", "")
    payload = build_bytes([
        ExportRow(a.work_date, personnel_number, a.wbs_element, a.hours)
        for a in allocations
    ])
    filename = suggest_filename(days[0][0], days[-1][0])
    return Response(
        content=payload,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Row-Count": str(len(allocations)),
            "X-Total-Hours": f"{sum(a.hours for a in allocations):.2f}",
        },
    )


@router.get("", summary="Export-Historie")
async def list_exports(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    dek: bytes = Depends(get_dek),
) -> list[dict]:
    return await store.list_exports(pool, int(user["sub"]), dek)


@router.get("/{export_id}/download", summary="Erzeugte XLSX herunterladen")
async def download_export(
    export_id: int,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    dek: bytes = Depends(get_dek),
) -> Response:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT stored_path, payload_enc FROM export WHERE id = $1 AND user_id = $2",
            export_id, int(user["sub"]),
        )
    if not row:
        raise HTTPException(404, "Export nicht gefunden.")
    if not os.path.exists(row["stored_path"]):
        raise HTTPException(410, "Die Datei ist auf dem Server nicht mehr vorhanden.")

    meta = crypto.decrypt_json(dek, bytes(row["payload_enc"])) if row["payload_enc"] else {}
    filename = meta.get("filename", f"export-{export_id}.xlsx")
    try:
        content = crypto.decrypt_file(dek, row["stored_path"])
    except crypto.CryptoError:
        raise HTTPException(500, "Die Datei konnte nicht entschluesselt werden.")
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _affected_rows(status: str) -> int:
    """asyncpg liefert den Kommando-Status, z.B. 'UPDATE 18'."""
    return int(status.rsplit(" ", 1)[-1]) if status else 0


@router.post("/{export_id}/revoke", summary="Export zuruecknehmen")
async def revoke_export(
    export_id: int,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Setzt die Zeilen wieder auf offen -- fuer den Fall, dass die Datei in SAP
    nicht angekommen ist. Der Eintrag und die Datei bleiben in der Historie."""
    user_id = int(user["sub"])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM export WHERE id = $1 AND user_id = $2", export_id, user_id
        )
        if not row:
            raise HTTPException(404, "Export nicht gefunden.")
        status = await conn.execute(
            "UPDATE cats_entry SET export_id = NULL, exported_at = NULL "
            "WHERE export_id = $1 AND user_id = $2",
            export_id, user_id,
        )
    return {"export_id": export_id, "reopened": _affected_rows(status)}


@router.delete("/{export_id}", summary="Export aus der Historie loeschen")
async def delete_export(
    export_id: int,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Entfernt Eintrag und Datei.

    Der Buchungsstatus der Zeilen bleibt erhalten: was einmal in SAP gebucht
    wurde, darf durch das Aufraeumen der Historie nicht wieder als offen
    auftauchen und ein zweites Mal eingespielt werden. Nur der Verweis auf den
    geloeschten Export verschwindet. Wer die Zeilen wirklich zurueckholen will,
    nimmt den Export vorher zurueck.
    """
    user_id = int(user["sub"])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT stored_path FROM export WHERE id = $1 AND user_id = $2",
            export_id, user_id,
        )
        if not row:
            raise HTTPException(404, "Export nicht gefunden.")
        # export_id wird per ON DELETE SET NULL genullt, exported_at bleibt.
        await conn.execute("DELETE FROM export WHERE id = $1 AND user_id = $2",
                           export_id, user_id)
    _remove_file(row["stored_path"])
    return {"deleted": export_id}


@router.delete("", summary="Gesamte Export-Historie loeschen")
async def delete_all_exports(
    confirm: bool = Query(default=False, description="Muss true sein"),
    revoke: bool = Query(
        default=False,
        description="Zusaetzlich alle Zeilen wieder auf offen setzen",
    ),
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    if not confirm:
        raise HTTPException(400, "Loeschen muss mit confirm=true bestaetigt werden.")

    user_id = int(user["sub"])
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, stored_path FROM export WHERE user_id = $1", user_id
        )
        reopened = 0
        async with conn.transaction():
            if revoke:
                status = await conn.execute(
                    "UPDATE cats_entry SET export_id = NULL, exported_at = NULL "
                    "WHERE user_id = $1 AND exported_at IS NOT NULL",
                    user_id,
                )
                reopened = _affected_rows(status)
            await conn.execute("DELETE FROM export WHERE user_id = $1", user_id)

    for r in rows:
        _remove_file(r["stored_path"])
    return {"deleted": len(rows), "reopened": reopened}
