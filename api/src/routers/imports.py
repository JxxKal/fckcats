"""PDF-Import: Upload, Vorschau, Klaerfaelle, Uebernahme."""
from __future__ import annotations

import hashlib
import os
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from config import Config
from database import get_pool
from deps import app_config, get_current_user
from pdf_parser import parse_pdf
from recalc import exported_dates, recalculate
from routers.cats import load_config, weights_as_fractions

router = APIRouter(prefix="/api/imports", tags=["imports"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class Clarification(BaseModel):
    """Entscheidung des Users zu einem Klaerfall."""
    work_date: date
    action: str = Field(pattern="^(book|exclude)$")
    hours: float | None = Field(default=None, gt=0, le=24)
    # Wenn gesetzt, wird die Entscheidung fuer diesen Grundtext dauerhaft gemerkt.
    remember_reason: str | None = None


class CommitRequest(BaseModel):
    clarifications: list[Clarification] = Field(default_factory=list)
    # Muss True sein, wenn der Import Tage beruehrt, die bereits exportiert wurden.
    confirm_overwrite_exported: bool = False


def _user_dir(cfg: Config, user_id: int, kind: str) -> str:
    path = os.path.join(cfg.data_dir, str(user_id), kind)
    os.makedirs(path, exist_ok=True)
    return path


def _day_payload(d) -> dict:
    return {
        "day": d.day,
        "weekday": d.weekday,
        "work_date": d.work_date.isoformat() if d.work_date else None,
        "reason": d.reason,
        "time_from": d.time_from,
        "time_to": d.time_to,
        "hours_gross": d.hours_gross,
        "hours_target": d.hours_target,
        "hours_net": d.hours_net,
        "excluded": d.excluded,
        "unknown_reason": d.unknown_reason,
        "incomplete": d.incomplete,
        "bookable": d.bookable,
    }


@router.post("", summary="Zeitnachweis-PDF hochladen und auswerten")
async def upload_pdf(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    cfg: Config = Depends(app_config),
) -> dict:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Es werden nur PDF-Dateien angenommen.")

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Die Datei ist groesser als 20 MB.")
    if not payload.startswith(b"%PDF"):
        raise HTTPException(400, "Die Datei ist kein PDF.")

    user_id = int(user["sub"])
    digest = hashlib.sha256(payload).hexdigest()
    target = os.path.join(_user_dir(cfg, user_id, "uploads"), f"{digest[:16]}.pdf")
    with open(target, "wb") as f:
        f.write(payload)

    cfg_row = await load_config(pool, user_id)
    rules = dict(cfg_row["reason_rules"]) if cfg_row else {}

    try:
        sheet = parse_pdf(target, rules)
    except RuntimeError as e:
        raise HTTPException(422, str(e))

    if not sheet.days:
        raise HTTPException(422, "Im PDF wurden keine Tageszeilen gefunden.")

    warnings = list(sheet.warnings)
    if cfg_row and sheet.personnel_number and \
            sheet.personnel_number.lstrip("0") != cfg_row["personnel_number"].lstrip("0"):
        warnings.append(
            f"Die Personalnummer im PDF ({sheet.personnel_number}) weicht von der "
            f"Konfiguration ({cfg_row['personnel_number']}) ab."
        )

    parse_result = {
        "days": [_day_payload(d) for d in sheet.days],
        "warnings": warnings,
    }

    async with pool.acquire() as conn:
        upload_id = await conn.fetchval(
            """
            INSERT INTO uploads
                (user_id, filename, stored_path, sha256, period_month, period_year,
                 personnel_number, parse_result)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id
            """,
            user_id, file.filename, target, digest,
            sheet.month, sheet.year, sheet.personnel_number, parse_result,
        )

    locked = await exported_dates(pool, user_id)
    # Klaerfaelle zaehlen mit: sie koennen nach der Entscheidung gebucht werden.
    touched = [
        d.work_date for d in sheet.days
        if d.work_date and (d.bookable or d.unknown_reason or d.incomplete)
    ]

    return {
        "upload_id": upload_id,
        "personnel_number": sheet.personnel_number,
        "employee_name": sheet.employee_name,
        "month": sheet.month,
        "year": sheet.year,
        "days": parse_result["days"],
        "bookable_count": len(sheet.bookable_days),
        "bookable_hours": round(sum(d.hours_net for d in sheet.bookable_days), 2),
        "clarifications": [_day_payload(d) for d in sheet.clarifications],
        "warnings": warnings,
        "already_exported_dates": sorted(d.isoformat() for d in touched if d in locked),
    }


@router.get("", summary="Bisherige Uploads auflisten")
async def list_uploads(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, filename, period_month, period_year, uploaded_at, committed_at
            FROM uploads WHERE user_id = $1 ORDER BY uploaded_at DESC LIMIT 100
            """,
            int(user["sub"]),
        )
    return [dict(r) for r in rows]


@router.post("/{upload_id}/commit", summary="Import uebernehmen")
async def commit_upload(
    upload_id: int,
    body: CommitRequest,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    user_id = int(user["sub"])

    cfg_row = await load_config(pool, user_id)
    if not cfg_row:
        raise HTTPException(400, "Bitte zuerst die CATS-Config ausfuellen.")

    async with pool.acquire() as conn:
        upload = await conn.fetchrow(
            "SELECT * FROM uploads WHERE id = $1 AND user_id = $2", upload_id, user_id
        )
    if not upload:
        raise HTTPException(404, "Upload nicht gefunden.")

    days = upload["parse_result"].get("days", [])
    decisions = {c.work_date.isoformat(): c for c in body.clarifications}

    # Zu schreibende Tage einsammeln: automatisch buchbare plus geklaerte.
    to_write: list[tuple[date, float, str, str]] = []   # (datum, stunden, quelle, grund)
    unresolved: list[str] = []

    for d in days:
        iso = d.get("work_date")
        if not iso:
            continue
        if d["bookable"]:
            to_write.append((date.fromisoformat(iso), d["hours_net"], "pdf", d["reason"] or ""))
            continue
        if d["unknown_reason"] or d["incomplete"]:
            decision = decisions.get(iso)
            if not decision:
                unresolved.append(iso)
                continue
            if decision.action == "exclude":
                continue
            hours = decision.hours if decision.hours is not None else d.get("hours_net")
            if not hours:
                raise HTTPException(
                    400, f"Fuer den {iso} fehlt die Stundenangabe."
                )
            source = "pdf" if decision.hours is None else "manual"
            to_write.append((date.fromisoformat(iso), float(hours), source, d["reason"] or ""))

    if unresolved:
        raise HTTPException(
            409,
            {
                "message": "Es sind noch Klaerfaelle offen.",
                "unresolved_dates": unresolved,
            },
        )

    # Beruehrt der Import bereits exportierte Tage?
    locked = await exported_dates(pool, user_id)
    conflicts = sorted(d.isoformat() for d, *_ in to_write if d in locked)
    if conflicts and not body.confirm_overwrite_exported:
        raise HTTPException(
            409,
            {
                "message": "Der Import beruehrt Tage, die bereits exportiert wurden.",
                "exported_dates": conflicts,
                "hint": "Erneut senden mit confirm_overwrite_exported = true.",
            },
        )

    # Dauerhafte Regeln fuer Grundtexte merken.
    new_rules = dict(cfg_row["reason_rules"])
    for c in body.clarifications:
        if c.remember_reason:
            new_rules[c.remember_reason] = c.action

    async with pool.acquire() as conn:
        async with conn.transaction():
            for work_date, hours, source, reason in to_write:
                await conn.execute(
                    """
                    INSERT INTO workday (user_id, work_date, hours, source, reason_text, upload_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (user_id, work_date) DO UPDATE SET
                        hours       = EXCLUDED.hours,
                        source      = EXCLUDED.source,
                        reason_text = EXCLUDED.reason_text,
                        upload_id   = EXCLUDED.upload_id
                    """,
                    user_id, work_date, hours, source, reason, upload_id,
                )
            if new_rules != dict(cfg_row["reason_rules"]):
                await conn.execute(
                    "UPDATE cats_config SET reason_rules = $1 WHERE id = $2",
                    new_rules, cfg_row["id"],
                )
            await conn.execute(
                "UPDATE uploads SET committed_at = now() WHERE id = $1", upload_id
            )

    # Die Freigabe der exportierten Tage passiert in recalculate, damit sie
    # erst nach dem Archivieren greift.
    result = await recalculate(
        pool, user_id, weights_as_fractions(cfg_row), cfg_row["version"],
        release_dates=[date.fromisoformat(c) for c in conflicts],
    )
    return {
        "imported_days": len(to_write),
        "overwritten_exported_days": conflicts,
        "recalculation": result,
    }
