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
from distribution import MIN_BOOKABLE_HOURS
from pdf_parser import parse_text, pdf_to_text
from recalc import exported_dates, recalculate
from routers.cats import load_config, plan_from_config
from storage_mode import storage_mode_of

router = APIRouter(prefix="/api/imports", tags=["imports"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class Clarification(BaseModel):
    """Anpassung des Users zu einem Tag.

    Gilt fuer jeden Tag, nicht nur fuer Klaerfaelle: die Vorschau ist
    vollstaendig editierbar. Ohne Eintrag greift die automatische Einordnung
    aus dem PDF.
    """
    work_date: date
    action: str = Field(pattern="^(book|exclude)$")
    hours: float | None = Field(default=None, gt=0, le=24)
    # Wenn gesetzt, wird die Entscheidung fuer diesen Grundtext dauerhaft gemerkt.
    remember_reason: str | None = None


class CommitRequest(BaseModel):
    # 'adjustments' ist der treffendere Name; 'clarifications' bleibt gueltig.
    adjustments: list[Clarification] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)
    # Muss True sein, wenn der Import Tage beruehrt, die bereits exportiert wurden.
    confirm_overwrite_exported: bool = False

    @property
    def all_adjustments(self) -> list[Clarification]:
        """Beide Felder zusammen; spaetere Eintraege gewinnen."""
        merged: dict[date, Clarification] = {}
        for entry in [*self.clarifications, *self.adjustments]:
            merged[entry.work_date] = entry
        return list(merged.values())


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
    try:
        tmp_dir = tempfile.mkdtemp(prefix="fckcats-")
    except OSError as e:
        raise HTTPException(
            507,
            f"Kein temporaeres Verzeichnis anlegbar: {e.strerror or e}. "
            f"Meist ist die Platte des Hosts voll -- 'df -h' und "
            f"'docker system df' geben Auskunft.",
        )

    tmp_pdf = os.path.join(tmp_dir, "sheet.pdf")
    target: str | None = None
    try:
        try:
            with open(tmp_pdf, "wb") as f:
                f.write(payload)
        except OSError as e:
            raise HTTPException(
                507,
                f"Das PDF konnte nicht zwischengespeichert werden: "
                f"{e.strerror or e}. Meist ist die Platte des Hosts voll.",
            )
        try:
            sheet = parse_text(pdf_to_text(tmp_pdf), rules)
        except RuntimeError as e:
            raise HTTPException(422, str(e))
        except Exception as e:
            # Ein unerwartet aufgebautes PDF ist ein Problem der Datei, nicht
            # der Anwendung -- entsprechend melden statt 500.
            raise HTTPException(
                422,
                f"Das PDF konnte nicht ausgewertet werden ({type(e).__name__}: {e}). "
                f"Stammt es aus derselben Quelle wie die uebrigen Zeitnachweise?",
            )
        if mode == "persistent":
            try:
                target = os.path.join(_user_dir(cfg, user_id, "uploads"), f"{digest[:16]}.pdf")
                crypto.encrypt_file(dek, payload, target)
            except OSError as e:
                # Volle Platte oder fehlende Rechte am Volume -- als solches
                # melden, statt es als Anwendungsfehler auszugeben.
                raise HTTPException(
                    507,
                    f"Die Datei konnte nicht abgelegt werden: {e.strerror or e}. "
                    f"Bitte Plattenplatz und Schreibrechte des Volumes pruefen.",
                )
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
    adjustments = body.all_adjustments
    decisions = {c.work_date.isoformat(): c for c in adjustments}

    for entry in adjustments:
        if entry.action == "book" and entry.hours is not None \
                and entry.hours < MIN_BOOKABLE_HOURS:
            raise HTTPException(
                400,
                f"{entry.work_date.isoformat()}: {entry.hours:g} h liegen unter der "
                f"Mindestbuchung von {MIN_BOOKABLE_HOURS:g} h. CATS nimmt kleinere "
                f"Zeiten nicht an.",
            )

    # Zu schreibende Tage einsammeln. Eine Anpassung des Users hat immer
    # Vorrang vor der automatischen Einordnung aus dem PDF.
    to_write: list[tuple[date, float, str, str]] = []   # (datum, stunden, quelle, grund)
    # Tage, die dieses PDF abdeckt, die aber nicht gebucht werden. Sie muessen
    # verschwinden, falls aus einem frueheren Import noch etwas dasteht -- sonst
    # bliebe ein abgewaehlter Tag oder ein nachtraeglich als Urlaub gemeldeter
    # Tag gebucht.
    to_remove: list[date] = []
    unresolved: list[str] = []
    seen_dates: set[str] = set()

    for d in days:
        iso = d.get("work_date")
        if not iso:
            continue
        seen_dates.add(iso)
        decision = decisions.get(iso)

        if decision:
            if decision.action == "exclude":
                to_remove.append(date.fromisoformat(iso))
                continue
            hours = decision.hours if decision.hours is not None else d.get("hours_net")
            if not hours:
                raise HTTPException(400, f"Fuer den {iso} fehlt die Stundenangabe.")
            # Ein von Hand gesetzter Wert wird als solcher gekennzeichnet.
            source = "pdf" if decision.hours is None else "manual"
            to_write.append((date.fromisoformat(iso), float(hours), source, d["reason"] or ""))
            continue

        if d["bookable"]:
            to_write.append((date.fromisoformat(iso), d["hours_net"], "pdf", d["reason"] or ""))
            continue
        if d["unknown_reason"] or d["incomplete"]:
            unresolved.append(iso)
            continue
        # Automatisch ausgeschlossen, etwa Urlaub oder ein freier Tag.
        to_remove.append(date.fromisoformat(iso))

    # Tage, die im PDF gar nicht vorkommen, aber von Hand ergaenzt wurden.
    for entry in adjustments:
        iso = entry.work_date.isoformat()
        if iso in seen_dates or entry.action != "book":
            continue
        if not entry.hours:
            raise HTTPException(
                400, f"Fuer den ergaenzten Tag {iso} fehlt die Stundenangabe."
            )
        to_write.append((entry.work_date, float(entry.hours), "manual", ""))

    if unresolved:
        raise HTTPException(
            409,
            {
                "message": "Es sind noch Klaerfaelle offen.",
                "unresolved_dates": unresolved,
            },
        )

    # Beruehrt der Import bereits exportierte Tage? Auch das Entfernen zaehlt.
    locked = await exported_dates(pool, user_id)
    beruehrt = {d for d, *_ in to_write} | set(to_remove)
    conflicts = sorted(d.isoformat() for d in beruehrt if d in locked)
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
    for c in adjustments:
        if c.remember_reason:
            new_rules[c.remember_reason] = c.action

    async with pool.acquire() as conn:
        async with conn.transaction():
            await store.upsert_workdays(conn, user_id, dek, to_write, upload_id)
            if to_remove:
                # Erst archivieren, dann loeschen -- die alte Fassung bleibt
                # in der Historie einsehbar.
                await store.archive_entries(conn, user_id, dek, to_remove)
                await conn.execute(
                    "DELETE FROM cats_entry WHERE user_id = $1 AND work_date = ANY($2::date[])",
                    user_id, to_remove,
                )
                await conn.execute(
                    "DELETE FROM workday WHERE user_id = $1 AND work_date = ANY($2::date[])",
                    user_id, to_remove,
                )
            await conn.execute(
                "UPDATE uploads SET committed_at = now() WHERE id = $1", upload_id
            )
    if new_rules != dict(cfg_row.get("reason_rules", {})):
        await store.update_config_payload(pool, cfg_row["id"], dek, {
            **{k: v for k, v in cfg_row.items() if k not in ("id", "version")},
            "reason_rules": new_rules,
        })

    # Die Freigabe der exportierten Tage passiert in recalculate, damit sie
    # erst nach dem Archivieren greift.
    result = await recalculate(
        pool, user_id, dek, plan_from_config(cfg_row), cfg_row["version"],
        release_dates=[date.fromisoformat(c) for c in conflicts],
    )
    return {
        "imported_days": len(to_write),
        "removed_days": [d.isoformat() for d in sorted(to_remove)],
        "overwritten_exported_days": conflicts,
        "recalculation": result,
    }
