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
    display_name: str | None = None
    email: str | None = None


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=12)
    # Vorgabe: der Benutzer muss beim naechsten Login selbst ein Passwort setzen,
    # damit der Administrator es danach nicht mehr kennt.
    must_change: bool = True


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
    """Lokale und per SAML angelegte Benutzer, samt Umfang ihres Workspaces."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id, u.username, u.email, u.display_name, u.role, u.source,
                   u.active, u.must_change_password, u.created_at, u.last_login,
                   (SELECT count(*) FROM cats_entry e WHERE e.user_id = u.id)  AS entry_count,
                   (SELECT count(*) FROM export     x WHERE x.user_id = u.id)  AS export_count
            FROM users u
            ORDER BY u.username
            """
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
                role         = COALESCE($2, role),
                active       = COALESCE($3, active),
                display_name = COALESCE($4, display_name),
                email        = COALESCE($5, email)
            WHERE id = $1
            RETURNING id, username, role, active, display_name, email
            """,
            user_id, body.role, body.active, body.display_name, body.email,
        )
    if not row:
        raise HTTPException(404, "Benutzer nicht gefunden.")
    return dict(row)


@router.post("/users/{user_id}/password", summary="Passwort eines lokalen Benutzers setzen")
async def reset_password(
    user_id: int,
    body: PasswordReset,
    _admin: dict = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, source FROM users WHERE id = $1", user_id
        )
        if not row:
            raise HTTPException(404, "Benutzer nicht gefunden.")
        if row["source"] != "local":
            raise HTTPException(
                400,
                "Der Benutzer meldet sich per SAML an und hat hier kein Passwort. "
                "Das Kennwort verwaltet der Identity Provider.",
            )
        await conn.execute(
            "UPDATE users SET password_hash = $1, must_change_password = $2 WHERE id = $3",
            hash_password(body.new_password), body.must_change, user_id,
        )
    return {"id": user_id, "username": row["username"], "must_change": body.must_change}
