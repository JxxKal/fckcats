"""Konfiguration aus der Umgebung."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    postgres_dsn: str
    secret_key: str
    bootstrap_admin_password: str
    cert_dir: str
    data_dir: str
    typical_week_hours: float
    debug_errors: bool

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            postgres_dsn=os.environ.get(
                "POSTGRES_DSN",
                "postgresql://fckcats:fckcats@localhost:5432/fckcats",
            ),
            secret_key=os.environ["SECRET_KEY"],
            bootstrap_admin_password=os.environ.get(
                "BOOTSTRAP_ADMIN_PASSWORD", "change-me-on-first-login"
            ),
            cert_dir=os.environ.get("CERT_DIR", "/certs"),
            data_dir=os.environ.get("DATA_DIR", "/data"),
            typical_week_hours=float(os.environ.get("TYPICAL_WEEK_HOURS", "38")),
            # Legt bei unerwarteten Fehlern den Traceback in die Antwort. Nur
            # zur Fehlersuche gedacht, etwa auf Rechnern ohne bequemen Zugriff
            # auf das Container-Protokoll.
            debug_errors=os.environ.get("DEBUG_ERRORS", "false").lower() == "true",
        )
