"""CATS-Config je User: Personalnummer, WBS-Arbeitsvorrat, Grund-Entscheidungen.

Die Config ist versioniert. Jede Aenderung erzeugt eine neue Version, damit
nachvollziehbar bleibt, mit welcher Gewichtung eine Verteilung berechnet wurde.
"""
from __future__ import annotations

import re

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from config import Config
from database import get_pool
from deps import app_config, get_current_user
from distribution import MIN_SLICE_HOURS, minimum_weight_pct

router = APIRouter(prefix="/api/cats-config", tags=["cats-config"])

# Bekannte WBS-Formen, z.B. DEO1111-NP/PJ00-O51.0000 und DEO5555000/PQ00-A02.0000
WBS_PATTERN = re.compile(r"^[A-Z0-9]{3,}(?:-[A-Z0-9]+)?/[A-Z0-9]+-[A-Z0-9.]+$")

WEIGHT_SUM_TOLERANCE = 0.01


class WbsElement(BaseModel):
    wbs: str = Field(min_length=1, max_length=64)
    weight: float = Field(gt=0, le=100)

    @field_validator("wbs")
    @classmethod
    def strip_wbs(cls, v: str) -> str:
        return v.strip()


class CatsConfigRequest(BaseModel):
    personnel_number: str = Field(min_length=1, max_length=20)
    wbs_elements: list[WbsElement]
    reason_rules: dict[str, str] = Field(default_factory=dict)

    @field_validator("personnel_number")
    @classmethod
    def strip_number(cls, v: str) -> str:
        return v.strip()

    @field_validator("reason_rules")
    @classmethod
    def check_rules(cls, v: dict[str, str]) -> dict[str, str]:
        for key, decision in v.items():
            if decision not in ("book", "exclude"):
                raise ValueError(f"Ungueltige Entscheidung fuer '{key}': {decision}")
        return v


async def load_config(pool: asyncpg.Pool, user_id: int) -> asyncpg.Record | None:
    """Neueste Version der CATS-Config des Users."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM cats_config WHERE user_id = $1 ORDER BY version DESC LIMIT 1",
            user_id,
        )


def weights_as_fractions(cfg_row: asyncpg.Record) -> list[tuple[str, float]]:
    """[(wbs, 0..1)] fuer den Verteiler."""
    elements = cfg_row["wbs_elements"]
    total = sum(e["weight"] for e in elements)
    if total <= 0:
        return []
    return [(e["wbs"], e["weight"] / total) for e in elements]


def check_weights(elements: list[WbsElement], week_hours: float) -> list[str]:
    """Plausibilitaetswarnungen -- blockieren das Speichern nicht."""
    warnings: list[str] = []
    threshold = minimum_weight_pct(week_hours)
    for e in elements:
        if e.weight < threshold:
            needed = round(MIN_SLICE_HOURS / week_hours * 100, 1)
            warnings.append(
                f"{e.wbs}: {e.weight:g} % ergeben bei {week_hours:g} h Wochenarbeitszeit nur "
                f"{round(week_hours * e.weight / 100, 2)} h und liegen damit unter der "
                f"Mindestbuchung von {MIN_SLICE_HOURS:g} h. Mindestens {needed:g} % noetig, "
                f"sonst faellt das Element in vielen Wochen aus."
            )
    for e in elements:
        if not WBS_PATTERN.match(e.wbs):
            warnings.append(
                f"{e.wbs}: entspricht keinem bekannten WBS-Schema. Wird trotzdem gespeichert."
            )
    return warnings


@router.get("", summary="CATS-Config lesen")
async def get_config(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    cfg: Config = Depends(app_config),
) -> dict:
    row = await load_config(pool, int(user["sub"]))
    if not row:
        return {
            "configured": False,
            "personnel_number": "",
            "wbs_elements": [],
            "reason_rules": {},
            "version": 0,
            "typical_week_hours": cfg.typical_week_hours,
            "min_weight_pct": minimum_weight_pct(cfg.typical_week_hours),
        }
    return {
        "configured": True,
        "personnel_number": row["personnel_number"],
        "wbs_elements": row["wbs_elements"],
        "reason_rules": row["reason_rules"],
        "version": row["version"],
        "typical_week_hours": cfg.typical_week_hours,
        "min_weight_pct": minimum_weight_pct(cfg.typical_week_hours),
    }


@router.put("", summary="CATS-Config speichern (neue Version)")
async def save_config(
    body: CatsConfigRequest,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    cfg: Config = Depends(app_config),
) -> dict:
    if not body.wbs_elements:
        raise HTTPException(400, "Mindestens ein WBS-Element ist erforderlich.")

    total = sum(e.weight for e in body.wbs_elements)
    if abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
        raise HTTPException(
            400,
            f"Die Summe der Gewichtungen muss 100 % ergeben, aktuell sind es {total:g} %.",
        )

    seen = set()
    for e in body.wbs_elements:
        if e.wbs in seen:
            raise HTTPException(400, f"WBS-Element doppelt angegeben: {e.wbs}")
        seen.add(e.wbs)

    warnings = check_weights(body.wbs_elements, cfg.typical_week_hours)

    async with pool.acquire() as conn:
        async with conn.transaction():
            version = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM cats_config WHERE user_id = $1",
                int(user["sub"]),
            )
            await conn.execute(
                """
                INSERT INTO cats_config
                    (user_id, version, personnel_number, wbs_elements, reason_rules)
                VALUES ($1, $2, $3, $4, $5)
                """,
                int(user["sub"]),
                version,
                body.personnel_number,
                [e.model_dump() for e in body.wbs_elements],
                body.reason_rules,
            )

    return {"version": version, "warnings": warnings}
