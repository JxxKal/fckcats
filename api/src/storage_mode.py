"""Speichermodus je Benutzer.

'persistent'  Arbeitszeiten, Zieltabelle, hochgeladene PDFs und die Historie
              werden aufbewahrt -- verschluesselt.
'ephemeral'   Nichts davon wird abgelegt. Die Anwendung ist dann ein reines
              Import/Export-Werkzeug: das PDF wird ausgewertet, die Verteilung
              berechnet und die XLSX ausgeliefert. Was bereits in CATS gebucht
              wurde, muss der Benutzer selbst im Blick behalten.

Die CATS-Config bleibt in beiden Faellen erhalten -- ohne Personalnummer und
WBS-Vorrat waere jeder Durchgang eine Neuerfassung.
"""
from __future__ import annotations

import asyncpg

PERSISTENT = "persistent"
EPHEMERAL = "ephemeral"


async def storage_mode_of(pool: asyncpg.Pool, user_id: int) -> str:
    async with pool.acquire() as conn:
        mode = await conn.fetchval(
            "SELECT storage_mode FROM users WHERE id = $1", user_id
        )
    return mode or PERSISTENT


async def purge_user_data(pool: asyncpg.Pool, user_id: int, data_dir: str) -> dict:
    """Loescht alle gespeicherten Arbeitszeitdaten eines Benutzers.

    Die CATS-Config und der Datenschluessel bleiben. Wird beim Wechsel nach
    'ephemeral' aufgerufen -- eine Zustimmung zurueckzuziehen muss auch das
    bereits Gespeicherte entfernen, sonst waere sie wirkungslos.
    """
    import os
    import shutil

    counts: dict[str, int] = {}
    async with pool.acquire() as conn:
        async with conn.transaction():
            for table in ("cats_entry", "entry_history", "workday",
                          "uploads", "export", "distribution_run"):
                status = await conn.execute(
                    f"DELETE FROM {table} WHERE user_id = $1", user_id
                )
                counts[table] = int(status.rsplit(" ", 1)[-1]) if status else 0

    user_dir = os.path.join(data_dir, str(user_id))
    if os.path.isdir(user_dir):
        shutil.rmtree(user_dir, ignore_errors=True)
    return counts
