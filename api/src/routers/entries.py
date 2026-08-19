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

router = APIRouter(prefix="/api/entries", tags=["entries"])


@router.get("", summary="Zieltabelle lesen, nach ISO-Woche gruppiert")
async def list_entries(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    only_open: bool = Query(default=False),
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    dek: bytes = Depends(get_dek),
) -> dict:
    user_id = int(user["sub"])
    rows = await store.load_entries(pool, user_id, dek, date_from, date_to, only_open)
    workdays = {w["work_date"]: w for w in await store.load_workdays(pool, user_id, dek)}

    weeks: dict[tuple[int, int], dict] = {}
    for r in rows:
        iso = r["work_date"].isocalendar()
        key = (iso[0], iso[1])
        week = weeks.setdefault(key, {
            "iso_year": iso[0],
            "iso_week": iso[1],
            "hours": 0.0,
            "days": set(),
            "per_wbs": {},
            "rows": [],
            "open_rows": 0,
            "exported_rows": 0,
        })
        hours = float(r["hours"])
        week["hours"] = round(week["hours"] + hours, 2)
        week["days"].add(r["work_date"])
        week["per_wbs"][r["wbs_element"]] = round(
            week["per_wbs"].get(r["wbs_element"], 0.0) + hours, 2
        )
        if r["exported_at"] is None:
            week["open_rows"] += 1
        else:
            week["exported_rows"] += 1
        day = workdays.get(r["work_date"], {})
        week["rows"].append({
            "work_date": r["work_date"].isoformat(),
            "wbs_element": r["wbs_element"],
            "hours": hours,
            "exported": r["exported_at"] is not None,
            "export_id": r["export_id"],
            "exported_at": r["exported_at"].isoformat() if r["exported_at"] else None,
            "day_hours": float(day["hours"]) if day.get("hours") is not None else None,
            "day_source": day.get("source"),
        })

    out = []
    for key in sorted(weeks):
        w = weeks[key]
        w["days"] = len(w["days"])
        out.append(w)

    return {
        "weeks": out,
        "total_hours": round(sum(w["hours"] for w in out), 2),
        "open_hours": round(
            sum(r["hours"] for w in out for r in w["rows"] if not r["exported"]), 2
        ),
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
