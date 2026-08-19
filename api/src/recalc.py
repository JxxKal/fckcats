"""Neuberechnung der Zieltabelle.

Bereits exportierte Tage bleiben unangetastet -- sie sind in SAP gebucht und
duerfen sich nicht nachtraeglich aendern. Neu gerechnet wird nur der offene
Bestand, und zwar immer zusammenhaengend, damit der Wochenuebertrag greift.
"""
from __future__ import annotations

import random
from datetime import date

import asyncpg

import store
from distribution import DEFAULT_CANDIDATES, Plan, plan_range_best_of


async def exported_dates(pool: asyncpg.Pool, user_id: int) -> set[date]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT work_date FROM cats_entry "
            "WHERE user_id = $1 AND exported_at IS NOT NULL",
            user_id,
        )
    return {r["work_date"] for r in rows}


async def recalculate(
    pool: asyncpg.Pool,
    user_id: int,
    dek: bytes,
    plan: Plan,
    config_version: int,
    seed: int | None = None,
    release_dates: list[date] | None = None,
) -> dict:
    """Verteilt alle noch nicht exportierten Arbeitstage neu.

    Tage, die bereits in einem Export stecken, werden uebersprungen. Der Seed
    wird gespeichert, damit sich das Ergebnis reproduzieren laesst.

    release_dates: Tage, die trotz Export neu gerechnet werden sollen -- der
    Fall "korrigiertes PDF fuer einen bereits gebuchten Zeitraum". Sie werden
    erst archiviert und danach freigegeben, damit in der Historie erhalten
    bleibt, dass diese Zeilen schon in SAP gebucht waren.
    """
    if not plan.ops and not plan.projects:
        return {"rows": 0, "days": 0, "weeks": []}

    released = set(release_dates or ())
    locked = await exported_dates(pool, user_id) - released

    workdays = await store.load_workdays(pool, user_id, dek)

    open_days = [
        (r["work_date"], float(r["hours"]))
        for r in workdays
        if r["work_date"] not in locked
    ]
    if not open_days:
        return {"rows": 0, "days": 0, "weeks": []}

    if seed is not None:
        # Ausdruecklich angeforderter Seed: exakt diesen verwenden, damit sich
        # ein frueheres Ergebnis reproduzieren laesst.
        allocations, reports, seed = plan_range_best_of(
            open_days, plan, seed, candidates=1
        )
    else:
        allocations, reports, seed = plan_range_best_of(
            open_days, plan, random.randrange(1, 2**31 - DEFAULT_CANDIDATES),
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            run_id = await conn.fetchval(
                """
                INSERT INTO distribution_run
                    (user_id, config_version, seed, date_from, date_to)
                VALUES ($1, $2, $3, $4, $5) RETURNING id
                """,
                user_id, config_version, seed,
                min(d for d, _ in open_days), max(d for d, _ in open_days),
            )
            days = [d for d, _ in open_days]
            # Archivieren, solange exported_at noch gesetzt ist -- sonst ginge
            # verloren, dass diese Zeilen bereits exportiert waren.
            await store.archive_entries(conn, user_id, dek, days)
            if released:
                await conn.execute(
                    "UPDATE cats_entry SET export_id = NULL, exported_at = NULL "
                    "WHERE user_id = $1 AND work_date = ANY($2::date[])",
                    user_id, sorted(released),
                )
            await conn.execute(
                "DELETE FROM cats_entry WHERE user_id = $1 AND work_date = ANY($2::date[]) "
                "AND exported_at IS NULL",
                user_id, days,
            )
            await store.insert_entries(
                conn, user_id, dek,
                [(a.work_date, a.wbs_element, a.hours) for a in allocations],
                run_id,
            )

    return {
        "run_id": run_id,
        "seed": seed,
        "rows": len(allocations),
        "days": len(open_days),
        "weeks": [
            {
                "iso_year": r.iso_year,
                "iso_week": r.iso_week,
                "days": r.days,
                "hours": r.hours,
                "per_wbs": r.per_wbs,
                "target_per_wbs": r.target_per_wbs,
                "max_deviation_pp": round(r.max_deviation_pp, 1),
                "project_hours": r.project_hours,
                "ops_hours": r.ops_hours,
                "projects_capped": r.projects_capped,
                "ops_starved": r.ops_starved,
            }
            for r in reports
        ],
    }
