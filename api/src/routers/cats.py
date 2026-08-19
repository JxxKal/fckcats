"""CATS-Config je User: Personalnummer, WBS-Arbeitsvorrat, Grund-Entscheidungen.

Die Config ist versioniert. Jede Aenderung erzeugt eine neue Version, damit
nachvollziehbar bleibt, mit welcher Gewichtung eine Verteilung berechnet wurde.
"""
from __future__ import annotations

import re

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

import store
from config import Config
from database import get_pool
from deps import app_config, get_current_user
from distribution import MIN_SLICE_HOURS, Plan, minimum_weight_pct
from keys import get_dek

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


class ProjectElement(BaseModel):
    """WBS-Element mit einer Obergrenze in Stunden je Woche."""
    wbs: str = Field(min_length=1, max_length=64)
    max_hours_per_week: float = Field(gt=0, le=80)

    @field_validator("wbs")
    @classmethod
    def strip_wbs(cls, v: str) -> str:
        return v.strip()


class CatsConfigRequest(BaseModel):
    personnel_number: str = Field(min_length=1, max_length=20)
    # Operations: teilen sich nach Gewicht, was die Projekte uebrig lassen
    wbs_elements: list[WbsElement] = Field(default_factory=list)
    # Projekte: feste Obergrenze je Woche, werden zuerst bedient
    projects: list[ProjectElement] = Field(default_factory=list)
    priority: str = Field(default="projects", pattern="^(projects|operations)$")
    # Mindestanteil der Woche fuer Operations; greift nur bei Vorrang Operations
    operations_min_pct: float = Field(default=0, ge=0, le=100)
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


async def load_config(pool: asyncpg.Pool, user_id: int, dek: bytes) -> dict | None:
    """Neueste Version der CATS-Config des Users, entschluesselt."""
    return await store.load_config(pool, user_id, dek)


def plan_from_config(cfg: dict) -> Plan:
    """Baut aus der gespeicherten Config den Verteilplan."""
    elements = cfg.get("wbs_elements", [])
    total = sum(e["weight"] for e in elements)
    ops = [(e["wbs"], e["weight"] / total) for e in elements] if total > 0 else []
    return Plan(
        ops=ops,
        projects=[(p["wbs"], float(p["max_hours_per_week"]))
                  for p in cfg.get("projects", [])],
        priority=cfg.get("priority", "projects"),
        ops_min_pct=float(cfg.get("operations_min_pct", 0) or 0),
    )


def check_plan(
    body: "CatsConfigRequest", week_hours: float
) -> list[str]:
    """Plausibilitaetswarnungen -- blockieren das Speichern nicht."""
    warnings: list[str] = []

    project_hours = sum(p.max_hours_per_week for p in body.projects)
    if project_hours > week_hours:
        warnings.append(
            f"Die Projekt-Obergrenzen ergeben zusammen {project_hours:g} h und "
            f"ueberschreiten damit die typische Wochenarbeitszeit von {week_hours:g} h. "
            + ("Die Projekte werden anteilig gekuerzt, Operations geht in vollen "
               "Wochen leer aus."
               if body.priority == "projects" and body.wbs_elements else
               f"Die Projekte werden anteilig auf "
               f"{100 - body.operations_min_pct:g} % der Woche gekuerzt.")
        )
    elif body.wbs_elements and project_hours > 0:
        rest = week_hours - project_hours
        warnings.append(
            f"Nach den Projekten bleiben in einer vollen Woche rund {rest:g} h "
            f"fuer die gewichtete Verteilung."
        ) if rest < week_hours * 0.2 else None

    for p in body.projects:
        if p.max_hours_per_week < MIN_SLICE_HOURS:
            warnings.append(
                f"{p.wbs}: {p.max_hours_per_week:g} h je Woche liegen unter der "
                f"Mindestbuchung von {MIN_SLICE_HOURS:g} h und fallen in vielen "
                f"Wochen aus."
            )
        if not WBS_PATTERN.match(p.wbs):
            warnings.append(
                f"{p.wbs}: entspricht keinem bekannten WBS-Schema. Wird trotzdem gespeichert."
            )

    if body.priority == "operations" and body.operations_min_pct <= 0:
        warnings.append(
            "Vorrang liegt bei Operations, aber es ist kein Mindestanteil gesetzt. "
            "Ohne ihn wirkt der Vorrang nicht."
        )

    # Der Gewichtung steht in vollen Wochen nur der Rest nach den Projekten zur
    # Verfuegung -- daran bemisst sich auch die Mindestbuchung.
    ops_hours = max(0.0, week_hours - project_hours)
    elements = body.wbs_elements
    threshold = minimum_weight_pct(ops_hours) if ops_hours > 0 else 100.0
    for e in elements:
        if e.weight < threshold:
            needed = round(MIN_SLICE_HOURS / ops_hours * 100, 1) if ops_hours > 0 else 100.0
            warnings.append(
                f"{e.wbs}: {e.weight:g} % ergeben bei {ops_hours:g} h fuer die gewichtete "
                f"Verteilung nur {round(ops_hours * e.weight / 100, 2)} h und liegen damit "
                f"unter der Mindestbuchung von {MIN_SLICE_HOURS:g} h. Mindestens "
                f"{needed:g} % noetig, sonst faellt das Element in vielen Wochen aus."
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
    dek: bytes = Depends(get_dek),
) -> dict:
    row = await load_config(pool, int(user["sub"]), dek)
    if not row:
        return {
            "configured": False,
            "personnel_number": "",
            "wbs_elements": [],
            "projects": [],
            "priority": "projects",
            "operations_min_pct": 0,
            "reason_rules": {},
            "version": 0,
            "typical_week_hours": cfg.typical_week_hours,
            "min_weight_pct": minimum_weight_pct(cfg.typical_week_hours),
        }
    return {
        "configured": True,
        "personnel_number": row.get("personnel_number", ""),
        "wbs_elements": row.get("wbs_elements", []),
        "projects": row.get("projects", []),
        "priority": row.get("priority", "projects"),
        "operations_min_pct": row.get("operations_min_pct", 0),
        "reason_rules": row.get("reason_rules", {}),
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
    dek: bytes = Depends(get_dek),
) -> dict:
    if not body.wbs_elements and not body.projects:
        raise HTTPException(
            400, "Mindestens ein WBS-Element ist erforderlich, als Operations oder Projekt."
        )

    # Die Gewichte teilen den Rest nach den Projekten auf, muessen aber unter
    # sich weiterhin 100 % ergeben.
    if body.wbs_elements:
        total = sum(e.weight for e in body.wbs_elements)
        if abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
            raise HTTPException(
                400,
                f"Die Summe der Gewichtungen muss 100 % ergeben, aktuell sind es {total:g} %.",
            )

    seen: set[str] = set()
    for wbs in [e.wbs for e in body.wbs_elements] + [p.wbs for p in body.projects]:
        if wbs in seen:
            raise HTTPException(
                400,
                f"WBS-Element doppelt angegeben: {wbs}. Ein Element gehoert entweder "
                f"zu Operations oder zu den Projekten.",
            )
        seen.add(wbs)

    if body.priority == "operations" and body.operations_min_pct >= 100:
        raise HTTPException(
            400, "Der Mindestanteil fuer Operations muss unter 100 % liegen."
        )

    warnings = check_plan(body, cfg.typical_week_hours)

    version = await store.save_config(pool, int(user["sub"]), dek, {
        "personnel_number": body.personnel_number,
        "wbs_elements": [e.model_dump() for e in body.wbs_elements],
        "projects": [p.model_dump() for p in body.projects],
        "priority": body.priority,
        "operations_min_pct": body.operations_min_pct,
        "reason_rules": body.reason_rules,
    })
    return {"version": version, "warnings": warnings}
