"""PDF-Import: Upload, Vorschau, Klaerfaelle, Uebernahme."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

import crypto
import store
from config import Config
from database import get_pool
from deps import app_config, get_current_user
from keys import get_dek
from pdf_parser import parse_text, pdf_to_text
from recalc import exported_dates, recalculate
from routers.cats import load_config, weights_as_fractions
from storage_mode import storage_mode_of

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
    dek: bytes = Depends(get_dek),
) -> dict:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Es werden nur PDF-Dateien angenommen.")

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Die Datei ist groesser als 20 MB.")
    if not payload.startswith(b"%PDF"):
        raise HTTPException(400, "Die Datei ist kein PDF.")

    user_id = int(user["sub"])
    mode = await storage_mode_of(pool, user_id)
    digest = hashlib.sha256(payload).hexdigest()

    cfg_row = await load_config(pool, user_id, dek)
    rules = dict(cfg_row.get("reason_rules", {})) if cfg_row else {}

    # Auch fuer die Auswertung braucht pdftotext eine Datei. Im Modus
    # 'ephemeral' liegt sie nur waehrend des Aufrufs in einem temporaeren
    # Verzeichnis und wird danach entfernt; nichts bleibt auf der Platte.
    tmp_dir = tempfile.mkdtemp(prefix="fckcats-")
    tmp_pdf = os.path.join(tmp_dir, "sheet.pdf")
    target: str | None = None
    try:
        with open(tmp_pdf, "wb") as f:
            f.write(payload)
        try:
            sheet = parse_text(pdf_to_text(tmp_pdf), rules)
        except RuntimeError as e:
            raise HTTPException(422, str(e))
        if mode == "persistent":
            target = os.path.join(_user_dir(cfg, user_id, "uploads"), f"{digest[:16]}.pdf")
            crypto.encrypt_file(dek, payload, target)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if not sheet.days:
        raise HTTPException(422, "Im PDF wurden keine Tageszeilen gefunden.")

    warnings = list(sheet.warnings)
    if cfg_row and sheet.personnel_number and \
            sheet.personnel_number.lstrip("0") != str(
                cfg_row.get("personnel_number", "")).lstrip("0"):
        warnings.append(
            f"Die Personalnummer im PDF ({sheet.personnel_number}) weicht von der "
            f"Konfiguration ({cfg_row.get('personnel_number')}) ab."
        )

    days_payload = [_day_payload(d) for d in sheet.days]
    upload_id = None

    if mode == "persistent":
        record = {
            "filename": file.filename,
            "personnel_number": sheet.personnel_number,
            "period_month": sheet.month,
            "period_year": sheet.year,
            "days": days_payload,
            "warnings": warnings,
        }
        async with pool.acquire() as conn:
            upload_id = await conn.fetchval(
                """
                INSERT INTO uploads (user_id, stored_path, sha256, payload_enc)
                VALUES ($1, $2, $3, $4) RETURNING id
                """,
                user_id, target or "", digest, crypto.encrypt_json(dek, record),
            )

    locked = await exported_dates(pool, user_id) if mode == "persistent" else set()
    # Klaerfaelle zaehlen mit: sie koennen nach der Entscheidung gebucht werden.
    touched = [
        d.work_date for d in sheet.days
        if d.work_date and (d.bookable or d.unknown_reason or d.incomplete)
    ]

    return {
        "upload_id": upload_id,
        "storage_mode": mode,
        "personnel_number": sheet.personnel_number,
        "employee_name": sheet.employee_name,
        "month": sheet.month,
        "year": sheet.year,
        "days": days_payload,
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
    dek: bytes = Depends(get_dek),
) -> list[dict]:
    return await store.list_uploads(pool, int(user["sub"]), dek)


@router.post("/{upload_id}/commit", summary="Import uebernehmen")
async def commit_upload(
    upload_id: int,
    body: CommitRequest,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    dek: bytes = Depends(get_dek),
) -> dict:
    user_id = int(user["sub"])

    if await storage_mode_of(pool, user_id) != "persistent":
        raise HTTPException(
            409,
            "Für diesen Workspace ist die Speicherung abgeschaltet. "
            "Der Export erfolgt direkt aus der Vorschau.",
        )

    cfg_row = await load_config(pool, user_id, dek)
    if not cfg_row:
        raise HTTPException(400, "Bitte zuerst die CATS-Config ausfuellen.")

    upload = await store.load_upload(pool, upload_id, user_id, dek)
    if not upload:
        raise HTTPException(404, "Upload nicht gefunden.")

    days = upload.get("days", [])
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
    new_rules = dict(cfg_row.get("reason_rules", {}))
    for c in body.clarifications:
        if c.remember_reason:
            new_rules[c.remember_reason] = c.action

    async with pool.acquire() as conn:
        async with conn.transaction():
            await store.upsert_workdays(conn, user_id, dek, to_write, upload_id)
            await conn.execute(
                "UPDATE uploads SET committed_at = now() WHERE id = $1", upload_id
            )
    if new_rules != dict(cfg_row.get("reason_rules", {})):
        await store.update_config_payload(pool, cfg_row["id"], dek, {
            "personnel_number": cfg_row.get("personnel_number", ""),
            "wbs_elements": cfg_row.get("wbs_elements", []),
            "reason_rules": new_rules,
        })

    # Die Freigabe der exportierten Tage passiert in recalculate, damit sie
    # erst nach dem Archivieren greift.
    result = await recalculate(
        pool, user_id, dek, weights_as_fractions(cfg_row), cfg_row["version"],
        release_dates=[date.fromisoformat(c) for c in conflicts],
    )
    return {
        "imported_days": len(to_write),
        "overwritten_exported_days": conflicts,
        "recalculation": result,
    }
