"""Tests fuer den Zeitnachweis-Parser.

Alle Testdaten sind synthetisch, folgen aber exakt dem Spaltenlayout, das
`pdftotext -layout` bei den echten Zeitnachweisen erzeugt.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pdf_parser  # noqa: E402

HEADER = (
    "Zeitnachweis                                                       01.04.2026 Seite 1 / 2\n"
    "\n"
    "00123456 Erika Mustermann\n"
    "\n"
    "Monat: Maerz - 2026\n"
    "\n"
    "Einzelergebnisse pro Tag\n"
    "Tag                        von      bis      Std.      Sollz     Verfall   GLZ       Mehrz    Prod\n"
    "\n"
)


def sheet(*lines, rules=None):
    return pdf_parser.parse_text(HEADER + "\n".join(lines) + "\n", rules)


def day(s, num):
    return next(d for d in s.days if d.day == num)


def test_kopfdaten():
    s = sheet("02 MO                       08:00    16:30      8,50      7,50               0,50      0,00      8,00")
    assert s.personnel_number == "00123456"
    assert s.employee_name == "Erika Mustermann"
    assert (s.month, s.year) == (3, 2026)


def test_normaler_tag_nutzt_prod_nicht_std():
    s = sheet("02 MO                       08:00    16:30      8,50      7,50               0,50      0,00      8,00")
    d = day(s, 2)
    assert d.hours_gross == 8.50      # Brutto inkl. Pause
    assert d.hours_net == 8.00        # Buchungsbasis
    assert d.bookable


def test_urlaub_wird_ausgeschlossen_trotz_std_wert():
    # Die Faustregel-Falle: Urlaub traegt 7,50 in Std., aber 0,00 in Prod.
    s = sheet("03 DI   Urlaub              08:30    16:00      7,50      7,50               0,00      0,00      0,00")
    d = day(s, 3)
    assert d.reason == "Urlaub"
    assert d.excluded and not d.bookable


def test_au_und_wildcards():
    s = sheet(
        "04 MI   AU ohne Attest      08:30    16:00      7,50      7,50               0,00      0,00      0,00",
        "05 DO   Arbeitsunfähig      08:30    16:00      7,50      7,50               0,00      0,00      0,00",
        "06 FR   Zeitausgleich ganz  08:30    16:00      7,50      7,50               0,00      0,00      0,00",
    )
    assert all(day(s, n).excluded for n in (4, 5, 6))


def test_wochenende_ohne_eintrag_ist_kein_klaerfall():
    s = sheet("07 SA   Frei laut AZP", "08 SO")
    assert day(s, 7).excluded
    assert day(s, 8).excluded
    assert not s.clarifications


def test_negatives_vorzeichen_nachgestellt():
    s = sheet("09 MO                       07:56    15:00      7,06      7,50               0,94-     0,00      6,56")
    assert day(s, 9).hours_net == 6.56


def test_unbekannter_grund_wird_klaerfall():
    s = sheet("10 DI   Dienstreise         08:00    16:00      8,00      7,50               0,00      0,00      7,50")
    d = day(s, 10)
    assert d.unknown_reason and not d.bookable
    assert d in s.clarifications


def test_gespeicherte_regel_loest_klaerfall_auf():
    line = "10 DI   Dienstreise         08:00    16:00      8,00      7,50               0,00      0,00      7,50"
    assert sheet(line, rules={"Dienstreise": "book"}).bookable_days[0].hours_net == 7.50
    assert day(sheet(line, rules={"Dienstreise": "exclude"}), 10).excluded


def test_vergessene_buchung_wird_klaerfall_mit_sollzeit():
    # Kommen und Gehen fehlen, nur die Sollzeit steht da.
    s = sheet("11 MI                                                     7,50")
    d = day(s, 11)
    assert d.reason == ""              # '7,50' darf nicht als Grundtext gelten
    assert d.incomplete and not d.bookable
    assert d.hours_target == 7.50      # Vorschlag fuer den Klaerfall-Dialog


def test_nur_kommen_gebucht_ist_klaerfall():
    s = sheet("12 DO                       08:12                                   7,50")
    d = day(s, 12)
    assert d.incomplete and not d.bookable


def test_datumszuordnung():
    s = sheet("13 FR                       08:00    16:30      8,50      7,50               0,50      0,00      8,00")
    assert day(s, 13).work_date.isoformat() == "2026-03-13"


def test_ausschlussliste_case_insensitiv():
    assert pdf_parser.is_excluded_reason("URLAUB")
    assert pdf_parser.is_excluded_reason("frei laut azp")
    assert pdf_parser.is_excluded_reason("Zeitausgleich halber Tag")   # Praefix
    assert not pdf_parser.is_excluded_reason("Dienstreise")
    assert not pdf_parser.is_excluded_reason("")


def test_umlaut_schreibweisen_treffen_dieselbe_regel():
    # pdftotext liefert Umlaute je nach Font-Encoding unterschiedlich.
    assert pdf_parser.is_excluded_reason("Arbeitsunfähig")
    assert pdf_parser.is_excluded_reason("Arbeitsunfaehig")
    assert pdf_parser.is_excluded_reason("ARBEITSUNFÄHIG ganztags")
