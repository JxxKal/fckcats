"""Verwaltung der Datenschluessel je Benutzer."""
from __future__ import annotations

import asyncpg
from fastapi import Depends, Header, HTTPException

import crypto
from database import get_pool
from deps import get_current_user

# Der Client schickt den ausgewickelten Schluessel hierin mit, wenn der
# Benutzer eine eigene Passphrase gesetzt hat.
DATA_KEY_HEADER = "X-Data-Key"


async def ensure_user_key(conn: asyncpg.Connection, user_id: int) -> bytes:
    """Liefert den DEK des Benutzers und legt ihn beim ersten Mal an."""
    row = await conn.fetchrow("SELECT * FROM user_key WHERE user_id = $1", user_id)
    if row:
        if row["wrap_mode"] != "master":
            raise HTTPException(
                412,
                "Für diesen Workspace ist eine Passphrase gesetzt. "
                "Sie wird zum Entsperren benötigt.",
            )
        wrap = crypto.derive_master_wrap_key(
            crypto.master_key_from_env(), bytes(row["kdf_salt"])
        )
        return crypto.unwrap_dek(bytes(row["wrapped_dek"]), wrap)

    dek = crypto.new_dek()
    salt = crypto.new_salt()
    wrap = crypto.derive_master_wrap_key(crypto.master_key_from_env(), salt)
    await conn.execute(
        "INSERT INTO user_key (user_id, wrapped_dek, wrap_mode, kdf_salt) "
        "VALUES ($1, $2, 'master', $3) ON CONFLICT (user_id) DO NOTHING",
        user_id, crypto.wrap_dek(dek, wrap), salt,
    )
    return dek


async def key_mode(pool: asyncpg.Pool, user_id: int) -> str:
    async with pool.acquire() as conn:
        mode = await conn.fetchval(
            "SELECT wrap_mode FROM user_key WHERE user_id = $1", user_id
        )
    return mode or "master"


async def unlock_with_passphrase(
    pool: asyncpg.Pool, user_id: int, passphrase: str
) -> bytes:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM user_key WHERE user_id = $1", user_id)
    if not row:
        raise HTTPException(404, "Für diesen Workspace gibt es keinen Schlüssel.")
    if row["wrap_mode"] != "passphrase":
        raise HTTPException(400, "Für diesen Workspace ist keine Passphrase gesetzt.")
    wrap = crypto.derive_passphrase_wrap_key(passphrase, bytes(row["kdf_salt"]))
    try:
        return crypto.unwrap_dek(bytes(row["wrapped_dek"]), wrap)
    except crypto.CryptoError:
        raise HTTPException(401, "Passphrase ist falsch.")


async def get_dek(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    x_data_key: str | None = Header(default=None, alias=DATA_KEY_HEADER),
) -> bytes:
    """Datenschluessel des angemeldeten Benutzers.

    Ohne Passphrase wickelt ihn der Master-Schluessel aus. Mit Passphrase muss
    der Client den beim Entsperren erhaltenen Schluessel mitschicken -- der
    Server bewahrt ihn zwischen zwei Anfragen nicht auf.
    """
    user_id = int(user["sub"])
    mode = await key_mode(pool, user_id)

    if mode == "passphrase":
        if not x_data_key:
            raise HTTPException(
                412,
                "Der Workspace ist mit einer Passphrase gesperrt. "
                "Bitte zuerst entsperren.",
            )
        try:
            dek = bytes.fromhex(x_data_key)
        except ValueError:
            raise HTTPException(400, "Ungültiger Schlüssel im Kopfzeilenfeld.")
        if len(dek) != crypto.DEK_BYTES:
            raise HTTPException(400, "Schlüssel hat die falsche Länge.")
        return dek

    async with pool.acquire() as conn:
        return await ensure_user_key(conn, user_id)
