"""Zieltabelle: anzeigen, neu berechnen, Historie."""
from __future__ import annotations

from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

import store
from database import get_pool
from deps import get_current_user
from keys import get_dek
from recalc import recalculate
from routers.cats import load_config, plan_from_config
from weekview import group_by_week

router = APIRouter(prefix="/api/entries", tags=["entries"])


@router.get("", summary="Zieltabelle lesen, nach ISO-Woche gruppiert")
async def list_entries(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    only_open: bool = Query(
        default=True,
        description="Exportierte Zeilen ausblenden -- die Zieltabelle zeigt, "
                    "was noch zu exportieren ist",
    ),
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    dek: bytes = Depends(get_dek),
) -> dict:
    """Die Zieltabelle ist der offene Bestand.

    Exportierte Zeilen sind gebucht und damit erledigt; sie verschwinden hier,
    sonst mischten sich die Zeilen eines abgeschlossenen Imports mit denen
    eines neuen. Nachzusehen sind sie in der Export-Historie, zurueckzuholen
    ueber die Ruecknahme des Exports. Mit ``only_open=false`` zeigt die Antwort
    wieder alles.
    """
    user_id = int(user["sub"])
    # Bewusst alles laden, auch das Exportierte: die Sicht braucht dessen
    # Stunden, um die erfasste Zeit des Tages richtig zu verrechnen.
    rows = await store.load_entries(pool, user_id, dek, date_from, date_to)
    workdays = await store.load_workdays(pool, user_id, dek)
    return {
        **group_by_week(rows, workdays, only_open, date_from, date_to),
        "only_open": only_open,
    }


@router.post("/recalculate", summary="Offene Zeilen neu verteilen")
async def recalculate_entries(
    seed: int | None = Query(default=None, description="Fuer reproduzierbare Laeufe"),
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    dek: bytes = Depends(get_dek),
) -> dict:
    user_id = int(user["sub"])
    cfg_row = await load_config(pool, user_id, dek)
    if not cfg_row:
        raise HTTPException(400, "Bitte zuerst die CATS-Config ausfuellen.")
    return await recalculate(
        pool, user_id, dek, plan_from_config(cfg_row), cfg_row["version"], seed
    )


@router.get("/history", summary="Ersetzte Zeilen einsehen")
async def entry_history(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    dek: bytes = Depends(get_dek),
) -> list[dict]:
    return await store.load_history(pool, int(user["sub"]), dek)


@router.delete("/history", summary="Aenderungshistorie loeschen")
async def clear_entry_history(
    confirm: bool = Query(default=False, description="Muss true sein"),
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Leert das Protokoll ersetzter Zeilen.

    Betrifft ausschliesslich das Protokoll -- Zieltabelle, Buchungsstatus und
    Export-Historie bleiben unberuehrt.
    """
    if not confirm:
        raise HTTPException(400, "Loeschen muss mit confirm=true bestaetigt werden.")
    async with pool.acquire() as conn:
        status = await conn.execute(
            "DELETE FROM entry_history WHERE user_id = $1", int(user["sub"])
        )
    return {"deleted": int(status.rsplit(" ", 1)[-1]) if status else 0}


@router.get("/workdays", summary="Validierte Tagesliste lesen")
async def list_workdays(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    dek: bytes = Depends(get_dek),
) -> list[dict]:
    return [
        {
            "work_date": w["work_date"].isoformat(),
            "hours": float(w["hours"]),
            "source": w.get("source"),
            "reason_text": w.get("reason_text"),
        }
        for w in await store.load_workdays(pool, int(user["sub"]), dek)
    ]
