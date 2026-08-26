"""Tests fuer den XLSX-Export gegen das CATS-Mass-Upload-Muster.

Geprueft wird die Datei selbst, nicht was eine Bibliothek daraus liest: genau
dort lag der Fehler. openpyxl las die alte Fassung anstandslos, SAP nicht --
weil die Texte als inline strings in den Zellen standen statt in der
Shared-String-Tabelle.
"""
import io
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xlsx_export import (  # noqa: E402
    COLUMNS, ExportRow, build_bytes, suggest_filename,
)

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

ROWS = [
    ExportRow(date(2026, 8, 17), "00123456", "DEO2222-NP/PJ00-O51.0000", 5.55),
    ExportRow(date(2026, 8, 17), "00123456", "DEO1111-NP/PJ00-O51.0000", 2.0),
]


def parts(rows=ROWS) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(build_bytes(rows)))


def shared_strings(z: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return [si.findtext("m:t", namespaces=NS) for si in root.findall("m:si", NS)]


def cells(z: zipfile.ZipFile) -> dict[str, tuple[str | None, str, str | None]]:
    """{'A4': (typ, wert, stil)} -- der Wert bleibt roh, wie er in der Datei steht."""
    root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    out = {}
    for c in root.iter(f"{{{NS['m']}}}c"):
        out[c.get("r")] = (c.get("t"), c.findtext("m:v", namespaces=NS), c.get("s"))
    return out


def value_of(z: zipfile.ZipFile, ref: str):
    """Zellwert, Textzellen ueber die Shared-String-Tabelle aufgeloest."""
    typ, raw, _ = cells(z)[ref]
    return shared_strings(z)[int(raw)] if typ == "s" else raw


# ── Die beiden Gruende, aus denen SAP die alte Datei ablehnte ────────────────

def test_texte_stehen_in_der_shared_string_tabelle():
    z = parts()
    blatt = z.read("xl/worksheets/sheet1.xml").decode()
    assert "inlineStr" not in blatt, "SAP liest Texte nur aus sharedStrings.xml"
    assert "<is>" not in blatt
    assert "DEO1111-NP/PJ00-O51.0000" in shared_strings(z)
    assert cells(z)["C4"][0] == "s"


def test_content_types_ist_der_erste_eintrag_im_archiv():
    """OPC verlangt es; strenge Leser weisen die Datei sonst ungelesen ab."""
    assert parts().namelist()[0] == "[Content_Types].xml"


# ── Aufbau nach dem Muster ───────────────────────────────────────────────────

def test_kopf_steht_dreimal_daten_ab_zeile_vier():
    z = parts()
    for row in (1, 2, 3):
        gelesen = [value_of(z, f"{chr(ord('A') + i)}{row}")
                   for i in range(len(COLUMNS))]
        assert gelesen == COLUMNS
    assert "A4" in cells(z)


def test_datum_wird_als_serial_mit_builtin_format_gespeichert():
    # Das Muster enthaelt 46251 fuer den 17.08.2026.
    z = parts()
    typ, wert, stil = cells(z)["A4"]
    assert typ is None and wert == "46251"          # Zahl, kein Text
    styles = ET.fromstring(z.read("xl/styles.xml"))
    xfs = styles.find("m:cellXfs", NS).findall("m:xf", NS)
    assert xfs[int(stil)].get("numFmtId") == "14"


def test_stunden_als_zahl_mit_zwei_nachkommastellen():
    z = parts()
    typ, wert, stil = cells(z)["H4"]
    assert typ is None and wert == "2"
    assert cells(z)["H5"][1] == "5.55"
    styles = ET.fromstring(z.read("xl/styles.xml"))
    xfs = styles.find("m:cellXfs", NS).findall("m:xf", NS)
    assert xfs[int(stil)].get("numFmtId") == "2"    # 0.00


def test_employee_ohne_fuehrende_nullen_als_zahl():
    z = parts()
    typ, wert, _ = cells(z)["B4"]
    assert typ is None and wert == "123456"


def test_nicht_numerische_personalnummer_bleibt_text():
    z = parts([ExportRow(date(2026, 8, 17), "AB1234", "DEO1111-NP/PJ00-O51.0000", 1.0)])
    assert cells(z)["B4"][0] == "s"
    assert value_of(z, "B4") == "AB1234"


def test_zeilen_nach_datum_und_wbs_sortiert():
    z = parts()
    assert value_of(z, "C4") == "DEO1111-NP/PJ00-O51.0000"
    assert value_of(z, "C5") == "DEO2222-NP/PJ00-O51.0000"


def test_optionale_spalten_bleiben_leer():
    vorhanden = cells(parts())
    for col in ("D", "E", "F", "G", "I", "J"):
        assert f"{col}4" not in vorhanden


def test_leerer_export_hat_nur_die_kopfzeilen():
    z = parts([])
    assert "A4" not in cells(z)
    blatt = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    assert blatt.find("m:dimension", NS).get("ref") == "A1:J3"


def test_sonderzeichen_werden_maskiert():
    z = parts([ExportRow(date(2026, 8, 17), "1", "A&B<C>", 1.0)])
    assert value_of(z, "C4") == "A&B<C>"


def test_derselbe_export_ergibt_dieselben_bytes():
    assert build_bytes(ROWS) == build_bytes(ROWS)


def test_dateiname():
    assert suggest_filename(date(2026, 8, 1), date(2026, 8, 17)) == "CATS_20260801_20260817.xlsx"
