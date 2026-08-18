"""Lokaler Login und Passwortwechsel.

Der lokale Zugang existiert, damit sich SAML ueberhaupt konfigurieren laesst:
ohne ihn gaebe es ein Henne-Ei-Problem, weil die SAML-Einstellungen einen
angemeldeten Admin voraussetzen. Im Normalbetrieb melden sich alle per SAML an.
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from passlib.context import CryptContext
from pydantic import BaseModel, Field

from config import Config
from database import get_pool
from deps import app_config, get_current_user
from jwt_utils import create_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


@router.post("/login", summary="Lokaler Login")
async def login(
    body: LoginRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    cfg: Config = Depends(app_config),
) -> dict:
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE username = $1 AND source = 'local'",
            body.username,
        )
    # Gleiche Fehlermeldung fuer unbekannten Benutzer und falsches Passwort,
    # damit sich keine Benutzernamen abfragen lassen.
    if not user or not user["password_hash"] or not verify_password(
        body.password, user["password_hash"]
    ):
        raise HTTPException(401, "Benutzername oder Passwort ist falsch.")
    if not user["active"]:
        raise HTTPException(403, "Benutzer ist deaktiviert.")

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET last_login = now() WHERE id = $1", user["id"])

    return {
        "token": create_token(
            cfg.secret_key, str(user["id"]), user["username"], user["role"],
            user["must_change_password"],
        ),
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "must_change_password": user["must_change_password"],
    }


@router.get("/me", summary="Angemeldeten Benutzer lesen")
async def me(
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, email, display_name, role, source, must_change_password "
            "FROM users WHERE id = $1",
            int(user["sub"]),
        )
    if not row:
        raise HTTPException(401, "Benutzer existiert nicht mehr.")
    return dict(row)


@router.post("/password", summary="Eigenes Passwort aendern")
async def change_password(
    body: PasswordChangeRequest,
    user: dict = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    cfg: Config = Depends(app_config),
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE id = $1 AND source = 'local'", int(user["sub"])
        )
        if not row:
            raise HTTPException(400, "Nur lokale Konten haben ein Passwort.")
        if not verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(401, "Aktuelles Passwort ist falsch.")
        if body.current_password == body.new_password:
            raise HTTPException(400, "Das neue Passwort muss sich vom alten unterscheiden.")
        await conn.execute(
            "UPDATE users SET password_hash = $1, must_change_password = FALSE WHERE id = $2",
            hash_password(body.new_password), row["id"],
        )
    # Neues Token, damit das Flag pwchange verschwindet.
    return {
        "token": create_token(
            cfg.secret_key, str(row["id"]), row["username"], row["role"], False
        )
    }
