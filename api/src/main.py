"""fckcats — Zeitnachweis-PDF zu CATS-Mass-Upload-XLSX."""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import crypto
import migrate
from config import Config
from database import close_pool, get_pool, init_pool
from routers import admin, auth, cats, entries, exports, imports, privacy, saml, ssl

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
        # Ohne Anzeigenamen: die Oberflaeche zeigt dann den Benutzernamen und
        # die Rolle als eigenes Kennzeichen, statt zweimal "Administrator".
        await conn.execute(
            """
            INSERT INTO users
                (username, password_hash, role, source, must_change_password)
            VALUES ('admin', $1, 'admin', 'local', TRUE)
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
    await migrate.run(get_pool())
    await migrate.encrypt_stored_files(get_pool(), cfg.data_dir)
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
    # Unterhalb von /api/, weil nginx nur diesen Pfad weiterreicht; sonst
    # fingen die Auslieferung der Oberflaeche die Doku-URLs ab.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.include_router(auth.router)
app.include_router(saml.router)
app.include_router(ssl.router)
app.include_router(cats.router)
app.include_router(imports.router)
app.include_router(entries.router)
app.include_router(exports.router)
app.include_router(privacy.router)
app.include_router(admin.router)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Faengt alles, was sonst als blankes "Internal Server Error" endet.

    Der Traceback bleibt im Protokoll, der Client bekommt nur eine Kennung.
    Damit laesst sich der Vorfall zuordnen, ohne Interna preiszugeben:

        docker compose logs api | grep <kennung>
    """
    error_id = uuid.uuid4().hex[:8]
    log.exception(
        "Unbehandelter Fehler [%s] bei %s %s",
        error_id, request.method, request.url.path,
    )
    return JSONResponse(
        {
            "detail": "Unerwarteter Fehler in der Anwendung. Die Einzelheiten stehen "
                      f"im Protokoll des api-Containers unter der Kennung {error_id}.",
            "error_id": error_id,
        },
        status_code=500,
    )


@app.exception_handler(crypto.CryptoError)
async def crypto_error_handler(_request: Request, exc: crypto.CryptoError) -> JSONResponse:
    """Ein unpassender Datenschluessel ist ein Bedienfehler, kein Serverfehler.

    Tritt auf, wenn ein Client einen falschen Schluessel mitschickt oder die
    Daten mit einem anderen Master-Schluessel verschluesselt wurden.
    """
    log.warning("Entschluesselung fehlgeschlagen: %s", exc)
    return JSONResponse(
        {"detail": "Die Daten konnten mit diesem Schluessel nicht entschluesselt "
                   "werden. Bitte den Workspace erneut entsperren."},
        status_code=409,
    )


@app.get("/api/health", tags=["system"], summary="Healthcheck")
async def health() -> JSONResponse:
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return JSONResponse({"status": "ok"})
    except Exception as e:  # pragma: no cover - nur im Fehlerfall relevant
        return JSONResponse({"status": "degraded", "detail": str(e)}, status_code=503)
