"""Administration: Benutzerverwaltung und SAML-Einstellungen."""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import get_pool
from deps import require_admin
from routers.auth import hash_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


class SamlSettings(BaseModel):
    enabled: bool = False
    idp_entity_id: str = ""
    idp_sso_url: str = ""
    idp_slo_url: str = ""
    idp_x509_cert: str = ""
    sp_entity_id: str = ""
    acs_url: str = ""
    slo_url: str = ""
    attribute_username: str = "uid"
    attribute_email: str = "email"
    attribute_display_name: str = "displayName"
    default_role: str = Field(default="user", pattern="^(admin|user)$")
    want_messages_signed: bool = False


class NewUser(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=12)
    display_name: str = ""
    email: str = ""
    role: str = Field(default="user", pattern="^(admin|user)$")


class UserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    active: bool | None = None


@router.get("/saml", summary="SAML-Einstellungen lesen")
async def get_saml(
    _admin: dict = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM system_config WHERE key = 'saml'")
    if not row:
        return SamlSettings().model_dump()
    settings = SamlSettings().model_dump() | dict(row["value"])
    # Das IdP-Zertifikat nicht im Klartext zurueckgeben.
    if settings.get("idp_x509_cert"):
        settings["idp_x509_cert"] = "__gespeichert__"
    return settings


@router.put("/saml", summary="SAML-Einstellungen speichern")
async def put_saml(
    body: SamlSettings,
    _admin: dict = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    payload = body.model_dump()

    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT value FROM system_config WHERE key = 'saml'")
        # Platzhalter bedeutet: das gespeicherte Zertifikat behalten.
        if payload["idp_x509_cert"] == "__gespeichert__" and existing:
            payload["idp_x509_cert"] = dict(existing["value"]).get("idp_x509_cert", "")

        if payload["enabled"]:
            missing = [
                f for f in ("idp_entity_id", "idp_sso_url", "idp_x509_cert",
                            "sp_entity_id", "acs_url")
                if not payload.get(f)
            ]
            if missing:
                raise HTTPException(
                    400, f"SAML kann nicht aktiviert werden, es fehlt: {', '.join(missing)}"
                )

        await conn.execute(
            """
            INSERT INTO system_config (key, value) VALUES ('saml', $1)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            payload,
        )
    return {"saved": True, "enabled": payload["enabled"]}


@router.get("/users", summary="Benutzer auflisten")
async def list_users(
    _admin: dict = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, username, email, display_name, role, source, active, "
            "created_at, last_login FROM users ORDER BY username"
        )
    return [dict(r) for r in rows]


@router.post("/users", summary="Lokalen Benutzer anlegen")
async def create_user(
    body: NewUser,
    _admin: dict = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM users WHERE username = $1", body.username
        )
        if exists:
            raise HTTPException(409, f"Benutzername '{body.username}' ist vergeben.")
        user_id = await conn.fetchval(
            """
            INSERT INTO users
                (username, email, display_name, password_hash, role, source, must_change_password)
            VALUES ($1, $2, $3, $4, $5, 'local', TRUE) RETURNING id
            """,
            body.username, body.email or None, body.display_name or body.username,
            hash_password(body.password), body.role,
        )
    return {"id": user_id, "username": body.username}


@router.patch("/users/{user_id}", summary="Benutzer aendern")
async def update_user(
    user_id: int,
    body: UserUpdate,
    admin: dict = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    if user_id == int(admin["sub"]) and body.active is False:
        raise HTTPException(400, "Das eigene Konto kann nicht deaktiviert werden.")

    async with pool.acquire() as conn:
        if body.role is not None and body.role != "admin":
            remaining = await conn.fetchval(
                "SELECT count(*) FROM users WHERE role = 'admin' AND active AND id <> $1",
                user_id,
            )
            if remaining == 0:
                raise HTTPException(400, "Der letzte Administrator kann nicht herabgestuft werden.")

        row = await conn.fetchrow(
            """
            UPDATE users SET
                role   = COALESCE($2, role),
                active = COALESCE($3, active)
            WHERE id = $1
            RETURNING id, username, role, active
            """,
            user_id, body.role, body.active,
        )
    if not row:
        raise HTTPException(404, "Benutzer nicht gefunden.")
    return dict(row)
