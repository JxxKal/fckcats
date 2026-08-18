#!/usr/bin/env python3
"""Erzeugt ein synthetisches Zeitnachweis-PDF fuer Integrationstests.

Der Inhalt ist frei erfunden, folgt aber exakt dem Spaltenlayout der echten
Zeitnachweise. Enthaelt bewusst alle Sonderfaelle: Urlaub, AU, Wochenende,
einen unbekannten Grundtext und einen Tag mit vergessener Buchung.

    python3 api/tests/make_fixture_pdf.py [zieldatei]
"""
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

LINES = [
    "Zeitnachweis                                                       01.04.2026 Seite 1 / 1",
    "",
    "00123456 Erika Mustermann",
    "",
    "Monat: Maerz - 2026",
    "",
    "Einzelergebnisse pro Tag",
    "Tag                        von      bis      Std.      Sollz     Verfall   GLZ       Mehrz    Prod",
    "",
    "02 MO                       08:00    16:30      8,50      7,50               0,50      0,00      8,00",
    "03 DI                       07:56    15:00      7,06      7,50               0,94-     0,00      6,56",
    "04 MI   Urlaub              08:30    16:00      7,50      7,50               0,00      0,00      0,00",
    "05 DO   Dienstreise         08:00    17:00      9,00      7,50               0,75      0,00      8,25",
    "06 FR                                                     7,50",
    "07 SA   Frei laut AZP",
    "08 SO   Frei laut AZP",
    "09 MO                       08:15    17:00      8,75      7,50               0,75      0,00      8,25",
    "10 DI   AU ohne Attest      08:30    16:00      7,50      7,50               0,00      0,00      0,00",
    "11 MI                       06:45    18:15     11,50      7,50               3,25      0,00     10,75",
    "12 DO                       08:28    16:30      8,02      7,50               0,02      0,00      7,52",
    "13 FR                       08:37    15:57      7,34      7,50               0,66-     0,00      6,84",
    "14 SA   Frei laut AZP",
    "15 SO   Frei laut AZP",
    "16 MO                       07:57    16:00      8,05      7,50               0,05      0,00      7,55",
    "",
    "Monatsuebersicht zum Stichtag 16.03.2026",
    "Produktivstunden Monat                     63,72",
    "Soll Tage Monat                            11,00 Anwesenheitstage Monat             8,00",
]


def build(target: Path) -> None:
    c = canvas.Canvas(str(target), pagesize=A4)
    # Dicktengleiche Schrift, damit pdftotext -layout die Spalten erhaelt.
    c.setFont("Courier", 7)
    y = A4[1] - 40
    for line in LINES:
        c.drawString(30, y, line)
        y -= 10
    c.save()


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1
               else Path(__file__).parent / "fixtures" / "zeitnachweis_beispiel.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    build(out)
    print(f"geschrieben: {out}")
