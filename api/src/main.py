"""fckcats — Zeitnachweis-PDF zu CATS-Mass-Upload-XLSX."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from config import Config
from database import close_pool, get_pool, init_pool
from routers import admin, auth, cats, entries, exports, imports, saml, ssl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("fckcats")

cfg = Config.from_env()

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


async def apply_schema() -> None:
    with open(SCHEMA_PATH) as f:
        schema = f.read()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(schema)
    log.info("Schema angewandt")


async def ensure_bootstrap_admin() -> None:
    """Legt beim ersten Start einen lokalen Admin an.

    Ohne ihn liesse sich SAML nie konfigurieren: die Einstellungen setzen einen
    angemeldeten Administrator voraus.
    """
    from routers.auth import hash_password

    pool = get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM users")
        if count:
            return
        await conn.execute(
            """
            INSERT INTO users
                (username, display_name, password_hash, role, source, must_change_password)
            VALUES ('admin', 'Administrator', $1, 'admin', 'local', TRUE)
            """,
            hash_password(cfg.bootstrap_admin_password),
        )
    log.warning(
        "Lokaler Administrator 'admin' angelegt. "
        "Das Passwort aus BOOTSTRAP_ADMIN_PASSWORD muss beim ersten Login geaendert werden."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool(cfg.postgres_dsn)
    await apply_schema()
    await ensure_bootstrap_admin()
    os.makedirs(cfg.data_dir, exist_ok=True)
    os.makedirs(cfg.cert_dir, exist_ok=True)
    log.info("fckcats-API bereit")
    yield
    await close_pool()


app = FastAPI(
    title="fckcats",
    description="Zeitnachweis-PDF zu CATS-Mass-Upload-XLSX",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(saml.router)
app.include_router(ssl.router)
app.include_router(cats.router)
app.include_router(imports.router)
app.include_router(entries.router)
app.include_router(exports.router)
app.include_router(admin.router)


@app.get("/api/health", tags=["system"], summary="Healthcheck")
async def health() -> JSONResponse:
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return JSONResponse({"status": "ok"})
    except Exception as e:  # pragma: no cover - nur im Fehlerfall relevant
        return JSONResponse({"status": "degraded", "detail": str(e)}, status_code=503)
