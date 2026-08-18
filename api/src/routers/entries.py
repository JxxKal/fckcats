"""Zieltabelle: anzeigen, neu berechnen, Historie."""
from __future__ import annotations

from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_pool
from deps import get_current_user
from recalc import recalculate
from routers.cats import load_config, weights_as_fractions

router = APIRouter(prefix="/api/entries", tags=["entries"])


@router.get("", summary="Zieltabelle lesen, nach ISO-Woche gruppiert")
async def list_entries(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    only_open: bool = Query(default=False),
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    user_id = int(user["sub"])
    clauses = ["e.user_id = $1"]
    params: list = [user_id]
    if date_from:
        params.append(date_from)
        clauses.append(f"e.work_date >= ${len(params)}")
    if date_to:
        params.append(date_to)
        clauses.append(f"e.work_date <= ${len(params)}")
    if only_open:
        clauses.append("e.export_id IS NULL")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT e.work_date, e.wbs_element, e.hours, e.export_id,
                   w.hours AS day_hours, w.source, w.reason_text
            FROM cats_entry e
            LEFT JOIN workday w
                   ON w.user_id = e.user_id AND w.work_date = e.work_date
            WHERE {' AND '.join(clauses)}
            ORDER BY e.work_date, e.wbs_element
            """,
            *params,
        )

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
        if r["export_id"] is None:
            week["open_rows"] += 1
        else:
            week["exported_rows"] += 1
        week["rows"].append({
            "work_date": r["work_date"].isoformat(),
            "wbs_element": r["wbs_element"],
            "hours": hours,
            "exported": r["export_id"] is not None,
            "export_id": r["export_id"],
            "day_hours": float(r["day_hours"]) if r["day_hours"] is not None else None,
            "day_source": r["source"],
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
) -> dict:
    user_id = int(user["sub"])
    cfg_row = await load_config(pool, user_id)
    if not cfg_row:
        raise HTTPException(400, "Bitte zuerst die CATS-Config ausfuellen.")
    return await recalculate(
        pool, user_id, weights_as_fractions(cfg_row), cfg_row["version"], seed
    )


@router.get("/history", summary="Ersetzte Zeilen einsehen")
async def entry_history(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT work_date, payload, was_exported, replaced_at
            FROM entry_history WHERE user_id = $1
            ORDER BY replaced_at DESC LIMIT 500
            """,
            int(user["sub"]),
        )
    return [dict(r) for r in rows]


@router.get("/workdays", summary="Validierte Tagesliste lesen")
async def list_workdays(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT work_date, hours, source, reason_text FROM workday "
            "WHERE user_id = $1 ORDER BY work_date",
            int(user["sub"]),
        )
    return [
        {
            "work_date": r["work_date"].isoformat(),
            "hours": float(r["hours"]),
            "source": r["source"],
            "reason_text": r["reason_text"],
        }
        for r in rows
    ]
