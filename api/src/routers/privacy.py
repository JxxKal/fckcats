"""Datenschutz-Einstellungen: Speichermodus und Verschluesselung."""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import crypto
import keys
from config import Config
from database import get_pool
from deps import app_config, get_current_user
from keys import get_dek
from storage_mode import EPHEMERAL, purge_user_data, storage_mode_of

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


class StorageModeRequest(BaseModel):
    mode: str = Field(pattern="^(persistent|ephemeral)$")
    # Beim Wechsel nach 'ephemeral' muss das bereits Gespeicherte weg, sonst
    # waere das Zuruecknehmen der Zustimmung wirkungslos.
    confirm_purge: bool = False


class PassphraseRequest(BaseModel):
    passphrase: str = Field(min_length=12)


class PassphraseChangeRequest(BaseModel):
    current_passphrase: str
    new_passphrase: str = Field(min_length=12)


@router.get("", summary="Datenschutz-Einstellungen lesen")
async def get_privacy(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    user_id = int(user["sub"])
    mode = await storage_mode_of(pool, user_id)
    wrap_mode = await keys.key_mode(pool, user_id)

    async with pool.acquire() as conn:
        counts = {
            "workdays": await conn.fetchval(
                "SELECT count(*) FROM workday WHERE user_id = $1", user_id),
            "entries": await conn.fetchval(
                "SELECT count(*) FROM cats_entry WHERE user_id = $1", user_id),
            "uploads": await conn.fetchval(
                "SELECT count(*) FROM uploads WHERE user_id = $1", user_id),
            "exports": await conn.fetchval(
                "SELECT count(*) FROM export WHERE user_id = $1", user_id),
            "history": await conn.fetchval(
                "SELECT count(*) FROM entry_history WHERE user_id = $1", user_id),
        }
    return {
        "storage_mode": mode,
        "encryption": wrap_mode,          # master | passphrase
        "stored": counts,
        "stored_total": sum(counts.values()),
    }


@router.put("/storage-mode", summary="Speicherung ein- oder ausschalten")
async def set_storage_mode(
    body: StorageModeRequest,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    cfg: Config = Depends(app_config),
) -> dict:
    user_id = int(user["sub"])
    current = await storage_mode_of(pool, user_id)
    if body.mode == current:
        return {"storage_mode": current, "purged": {}}

    purged: dict = {}
    if body.mode == EPHEMERAL:
        async with pool.acquire() as conn:
            stored = await conn.fetchval(
                "SELECT count(*) FROM workday WHERE user_id = $1", user_id
            )
        if stored and not body.confirm_purge:
            raise HTTPException(
                409,
                {
                    "message": "Es sind noch Daten gespeichert.",
                    "stored_workdays": stored,
                    "hint": "Erneut senden mit confirm_purge = true. "
                            "Die Daten werden dabei unwiderruflich geloescht.",
                },
            )
        purged = await purge_user_data(pool, user_id, cfg.data_dir)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET storage_mode = $1 WHERE id = $2", body.mode, user_id
        )
    return {"storage_mode": body.mode, "purged": purged}


@router.delete("/data", summary="Alle gespeicherten Arbeitszeitdaten loeschen")
async def purge_data(
    confirm: bool = Query(default=False, description="Muss true sein"),
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    cfg: Config = Depends(app_config),
) -> dict:
    """Loescht Arbeitszeiten, Zieltabelle, PDFs, Exporte und Historie.

    Die CATS-Config bleibt erhalten -- sie ist die Arbeitsgrundlage, nicht der
    gespeicherte Verlauf.
    """
    if not confirm:
        raise HTTPException(400, "Loeschen muss mit confirm=true bestaetigt werden.")
    return {"purged": await purge_user_data(pool, int(user["sub"]), cfg.data_dir)}


@router.post("/passphrase", summary="Eigene Passphrase setzen")
async def set_passphrase(
    body: PassphraseRequest,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    dek: bytes = Depends(get_dek),
) -> dict:
    """Wickelt den Datenschluessel ab sofort mit der Passphrase ein.

    Danach kommt niemand mehr ohne sie an die Daten -- auch der Betreiber
    nicht, der den Master-Schluessel besitzt. Geht die Passphrase verloren,
    sind die Daten unwiederbringlich verloren.
    """
    user_id = int(user["sub"])
    if await keys.key_mode(pool, user_id) == "passphrase":
        raise HTTPException(409, "Es ist bereits eine Passphrase gesetzt.")

    salt = crypto.new_salt()
    wrap = crypto.derive_passphrase_wrap_key(body.passphrase, salt)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_key SET wrapped_dek = $1, wrap_mode = 'passphrase', "
            "kdf_salt = $2, updated_at = now() WHERE user_id = $3",
            crypto.wrap_dek(dek, wrap), salt, user_id,
        )
    # Der Client haelt den Schluessel ab jetzt selbst und schickt ihn mit.
    return {"encryption": "passphrase", "data_key": dek.hex()}


@router.post("/passphrase/change", summary="Passphrase aendern")
async def change_passphrase(
    body: PassphraseChangeRequest,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    user_id = int(user["sub"])
    dek = await keys.unlock_with_passphrase(pool, user_id, body.current_passphrase)
    salt = crypto.new_salt()
    wrap = crypto.derive_passphrase_wrap_key(body.new_passphrase, salt)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_key SET wrapped_dek = $1, kdf_salt = $2, updated_at = now() "
            "WHERE user_id = $3",
            crypto.wrap_dek(dek, wrap), salt, user_id,
        )
    return {"encryption": "passphrase", "data_key": dek.hex()}


@router.delete("/passphrase", summary="Passphrase entfernen")
async def remove_passphrase(
    body: PassphraseRequest,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Nimmt die Passphrase zurueck; der Master-Schluessel uebernimmt wieder.

    Verlangt die aktuelle Passphrase -- sonst koennte ein uebernommenes Token
    den Zusatzschutz einfach abschalten.
    """
    user_id = int(user["sub"])
    dek = await keys.unlock_with_passphrase(pool, user_id, body.passphrase)
    salt = crypto.new_salt()
    wrap = crypto.derive_master_wrap_key(crypto.master_key_from_env(), salt)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_key SET wrapped_dek = $1, wrap_mode = 'master', "
            "kdf_salt = $2, updated_at = now() WHERE user_id = $3",
            crypto.wrap_dek(dek, wrap), salt, user_id,
        )
    return {"encryption": "master"}


@router.post("/unlock", summary="Workspace mit der Passphrase entsperren")
async def unlock(
    body: PassphraseRequest,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    dek = await keys.unlock_with_passphrase(pool, int(user["sub"]), body.passphrase)
    return {"data_key": dek.hex()}
