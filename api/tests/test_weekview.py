"""Tests fuer die Zieltabellen-Sicht.

Die Zusage lautet: nach einem Export ist die Zieltabelle leer. Was gebucht ist,
verschwindet aus der Anzeige -- samt der erfassten Zeit, die dazu gehoert, sonst
taucht die exportierte Stunde als "nicht gebucht" wieder auf. Erfasste Zeit ohne
Zeile bleibt dagegen sichtbar, damit nichts stillschweigend unter den Tisch
faellt.
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from weekview import group_by_week  # noqa: E402

EXPORTIERT = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)

# KW 33 ist exportiert, KW 34 frisch importiert.
ALT = date(2026, 8, 10)
NEU = date(2026, 8, 17)
NEU2 = date(2026, 8, 18)


def zeile(tag: date, wbs: str, hours: float, exported: bool = False) -> dict:
    return {
        "work_date": tag,
        "wbs_element": wbs,
        "hours": hours,
        "exported_at": EXPORTIERT if exported else None,
        "export_id": 7 if exported else None,
    }


def tag(d: date, hours: float) -> dict:
    return {"work_date": d, "hours": hours, "source": "pdf"}


def test_exportierte_woche_verschwindet_samt_erfasster_zeit():
    rows = [zeile(ALT, "A", 8.0, exported=True), zeile(NEU, "A", 8.0)]
    res = group_by_week(rows, [tag(ALT, 8.0), tag(NEU, 8.0)], hide_exported=True)

    assert [w["iso_week"] for w in res["weeks"]] == [NEU.isocalendar()[1]]
    # Die erfasste Zeit der alten Woche darf nicht als "nicht gebucht" auftauchen.
    assert res["recorded_hours"] == 8.0
    assert res["unbooked_hours"] == 0.0
    assert res["hidden_exported_rows"] == 1


def test_halb_exportierte_woche_rechnet_tagesgenau():
    """Der exportierte Montag darf den Rest der Woche nicht verfaelschen."""
    rows = [zeile(NEU, "A", 8.0, exported=True), zeile(NEU2, "A", 8.0)]
    res = group_by_week(rows, [tag(NEU, 8.0), tag(NEU2, 8.0)], hide_exported=True)

    week = res["weeks"][0]
    assert week["hours"] == 8.0            # nur der offene Dienstag
    assert week["recorded_hours"] == 8.0   # und nur dessen erfasste Zeit
    assert week["unbooked_hours"] == 0.0   # der Montag zaehlt nicht als ungebucht
    assert week["exported_rows"] == 1      # der Hinweis bleibt
    assert week["open_rows"] == 1


def test_teilweise_exportierter_tag_bringt_nur_den_rest_mit():
    rows = [zeile(NEU, "A", 5.0, exported=True), zeile(NEU, "B", 3.0)]
    res = group_by_week(rows, [tag(NEU, 8.0)], hide_exported=True)

    week = res["weeks"][0]
    assert week["hours"] == 3.0
    assert week["recorded_hours"] == 3.0
    assert week["unbooked_hours"] == 0.0


def test_nicht_gebuchte_zeit_bleibt_nicht_gebucht():
    """Was keinem WBS-Element zugeordnet ist, faellt nicht unter den Tisch."""
    rows = [zeile(NEU, "A", 6.0)]
    res = group_by_week(rows, [tag(NEU, 8.0)], hide_exported=True)

    assert res["weeks"][0]["unbooked_hours"] == 2.0


def test_woche_ohne_jede_zeile_bleibt_stehen():
    """Eine Woche nur aus nicht gebuchter Zeit ist nicht abgearbeitet."""
    res = group_by_week([], [tag(ALT, 7.5)], hide_exported=True)

    assert len(res["weeks"]) == 1
    assert res["weeks"][0]["hours"] == 0.0
    assert res["weeks"][0]["unbooked_hours"] == 7.5


def test_alles_exportiert_ergibt_eine_leere_zieltabelle():
    rows = [zeile(ALT, "A", 8.0, exported=True), zeile(NEU, "A", 8.0, exported=True)]
    res = group_by_week(rows, [tag(ALT, 8.0), tag(NEU, 8.0)], hide_exported=True)

    assert res["weeks"] == []
    assert res["hidden_exported_rows"] == 2
    assert res["open_hours"] == 0.0
    assert res["recorded_hours"] == 0.0


def test_ohne_ausblenden_bleibt_alles_sichtbar():
    """only_open=false: die exportierten Zeilen kommen mit."""
    rows = [zeile(ALT, "A", 8.0, exported=True), zeile(NEU, "A", 6.0)]
    res = group_by_week(rows, [tag(ALT, 8.0), tag(NEU, 6.0)], hide_exported=False)

    assert len(res["weeks"]) == 2
    assert res["total_hours"] == 14.0
    assert res["open_hours"] == 6.0
    assert res["weeks"][0]["exported_rows"] == 1
    assert res["hidden_exported_rows"] == 0


def test_zeitraumfilter_gilt_fuer_zeilen_und_tage():
    rows = [zeile(ALT, "A", 8.0, exported=True), zeile(NEU, "A", 8.0)]
    res = group_by_week(rows, [tag(ALT, 8.0), tag(NEU, 8.0)],
                        hide_exported=True, date_from=NEU)

    assert len(res["weeks"]) == 1
    # Die alte Woche liegt ausserhalb des Zeitraums und zaehlt nicht mit.
    assert res["hidden_exported_rows"] == 0
    assert res["recorded_hours"] == 8.0


def test_tagesstunden_und_quelle_haengen_an_der_zeile():
    res = group_by_week([zeile(NEU, "A", 6.0)], [tag(NEU, 8.0)], hide_exported=True)
    row = res["weeks"][0]["rows"][0]

    assert row["day_hours"] == 8.0
    assert row["day_source"] == "pdf"
    assert row["exported"] is False
