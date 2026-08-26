"""Die Zieltabelle als Sicht: Zeilen nach ISO-Woche gruppiert.

Die Zieltabelle zeigt den **offenen Bestand**. Eine exportierte Zeile ist in
SAP gebucht und damit erledigt; bliebe sie stehen, mischte sich der Inhalt
eines abgeschlossenen Imports mit dem eines neuen und die Anzeige waere nicht
mehr zu lesen. Nachzusehen sind exportierte Zeilen in der Export-Historie,
zurueckzuholen ueber die Ruecknahme des Exports.

Ausgeblendet wird nicht nur die Zeile, sondern auch die erfasste Zeit, die zu
ihr gehoert -- sonst taucht die exportierte Stunde als "nicht gebucht" wieder
auf. Gerechnet wird das je Tag, damit eine halb exportierte Woche stimmt:

  * Ein Tag, dessen Zeilen alle exportiert sind, faellt ganz heraus.
  * Ein Tag mit exportierten *und* offenen Zeilen bringt nur den Rest seiner
    erfassten Stunden mit.
  * Ein Tag ohne jede Zeile bleibt stehen. Er ist nicht abgearbeitet, sondern
    besteht ausschliesslich aus nicht gebuchter Zeit und soll sichtbar bleiben,
    damit erfasste Zeit nicht stillschweigend verschwindet.

Eine Woche, von der nach diesen Regeln nichts uebrig bleibt, verschwindet. Die
Anzahl der ausgeblendeten Zeilen wird je Woche mitgegeben, damit eine
angebrochene Woche nicht so aussieht, als fehle die halbe Zeit.
"""
from __future__ import annotations

from datetime import date


def _hours_of(day: dict | None) -> float | None:
    """Erfasste Stunden eines Tages; None, wenn der Tag keine traegt."""
    if not day or day.get("hours") is None:
        return None
    return float(day["hours"])


def _in_range(work_date: date, date_from: date | None, date_to: date | None) -> bool:
    if date_from and work_date < date_from:
        return False
    if date_to and work_date > date_to:
        return False
    return True


def group_by_week(
    rows: list[dict],
    workdays: list[dict],
    hide_exported: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Baut die Antwort der Zieltabelle.

    rows            alle Zeilen des Zeitraums, offene wie exportierte
    workdays        validierte Tage, auch solche ohne Zeile
    hide_exported   exportierte Zeilen samt ihrer erfassten Zeit auslassen
    """
    rows = [r for r in rows if _in_range(r["work_date"], date_from, date_to)]
    shown = [r for r in rows if not (hide_exported and r["exported_at"])]
    days = {
        w["work_date"]: w for w in workdays
        if _in_range(w["work_date"], date_from, date_to)
    }

    # Was je Tag ausgeblendet wird: Anzahl fuer den Hinweis, Stunden fuer die
    # Verrechnung mit der erfassten Zeit.
    hidden_rows: dict[date, int] = {}
    hidden_hours: dict[date, float] = {}
    for r in rows:
        if hide_exported and r["exported_at"]:
            hidden_rows[r["work_date"]] = hidden_rows.get(r["work_date"], 0) + 1
            hidden_hours[r["work_date"]] = round(
                hidden_hours.get(r["work_date"], 0.0) + float(r["hours"]), 2
            )

    weeks: dict[tuple[int, int], dict] = {}

    def week_of(work_date: date) -> dict:
        iso = work_date.isocalendar()
        return weeks.setdefault((iso[0], iso[1]), {
            "iso_year": iso[0],
            "iso_week": iso[1],
            "hours": 0.0,            # gebuchte Stunden
            "recorded_hours": 0.0,   # laut Zeitnachweis erfasst
            "days": set(),
            "per_wbs": {},
            "rows": [],
            "open_rows": 0,
            "exported_rows": 0,
        })

    for r in shown:
        week = week_of(r["work_date"])
        hours = float(r["hours"])
        week["hours"] = round(week["hours"] + hours, 2)
        week["days"].add(r["work_date"])
        week["per_wbs"][r["wbs_element"]] = round(
            week["per_wbs"].get(r["wbs_element"], 0.0) + hours, 2
        )
        if r["exported_at"] is None:
            week["open_rows"] += 1
        else:
            week["exported_rows"] += 1
        week["rows"].append({
            "work_date": r["work_date"].isoformat(),
            "wbs_element": r["wbs_element"],
            "hours": hours,
            "exported": r["exported_at"] is not None,
            "export_id": r["export_id"],
            "exported_at": r["exported_at"].isoformat() if r["exported_at"] else None,
            "day_hours": _hours_of(days.get(r["work_date"])),
            "day_source": (days.get(r["work_date"]) or {}).get("source"),
        })

    # Danach die erfasste Zeit -- auch fuer Tage, von denen wegen nicht
    # gebuchter Zeit gar keine Zeile uebrig blieb.
    for work_date, day in days.items():
        rest = round((_hours_of(day) or 0.0) - hidden_hours.get(work_date, 0.0), 2)
        if hidden_rows.get(work_date) and rest <= 0.005:
            # Der Tag ist abgearbeitet und taucht nicht mehr auf.
            continue
        week = week_of(work_date)
        week["recorded_hours"] = round(week["recorded_hours"] + max(0.0, rest), 2)

    for work_date, count in hidden_rows.items():
        iso = work_date.isocalendar()
        if (iso[0], iso[1]) in weeks:
            weeks[(iso[0], iso[1])]["exported_rows"] += count

    out = []
    for key in sorted(weeks):
        week = weeks[key]
        week["days"] = len(week["days"])
        # Was erfasst, aber keinem WBS-Element zugeordnet wurde.
        week["unbooked_hours"] = round(
            max(0.0, week["recorded_hours"] - week["hours"]), 2
        )
        out.append(week)

    return {
        "weeks": out,
        "total_hours": round(sum(w["hours"] for w in out), 2),
        "recorded_hours": round(sum(w["recorded_hours"] for w in out), 2),
        "unbooked_hours": round(sum(w["unbooked_hours"] for w in out), 2),
        "open_hours": round(
            sum(r["hours"] for w in out for r in w["rows"] if not r["exported"]), 2
        ),
        # Zeilen, die es gibt, die hier aber nicht auftauchen.
        "hidden_exported_rows": sum(hidden_rows.values()),
    }
