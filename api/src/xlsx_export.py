"""Erzeugt die CATS-Mass-Upload-XLSX.

Das Zielformat folgt exakt dem Muster (SPEC.md §6):

  * ein Sheet, Spalten A-J
  * die Kopfzeile steht dreimal (Zeile 1, 2, 3), Daten ab Zeile 4
  * WORKDATE  als echtes Excel-Datum, Zahlenformat numFmtId=14
  * EMPLOYEE  als Zahl ohne fuehrende Nullen
  * CATSHOURS als Zahl mit zwei Nachkommastellen
  * ABS_ATT_TYPE, REC_ORDER, ACTIVITY, WAGETYPE, ZZICTPC, SHORTTEXT bleiben leer

**Warum die Datei von Hand geschrieben wird und nicht mit openpyxl.** openpyxl
legt jeden Text als *inline string* ab (``<c t="inlineStr"><is><t>...``), SAP
liest Texte aber ausschliesslich aus der Shared-String-Tabelle. Das
WBS-Element ist die einzige Textspalte der Datenzeilen -- es kam beim Upload
leer an, und die Datei wurde abgelehnt. Wer sie einmal in Excel oeffnete und
speicherte, bekam die Tabelle geschenkt; dieser Umweg war die stille
Voraussetzung dafuer, dass ein Export in SAP ankam. Dazu verlangt OPC, dass
``[Content_Types].xml`` der erste Eintrag im ZIP ist -- openpyxl haengt ihn
ans Ende.

Die erzeugten Teile sind einer von SAP angenommenen Datei nachgebaut: Texte in
``sharedStrings.xml``, drei Zellformate (ohne / Datum / 0.00), leere Zellen
werden weggelassen. Das Ergebnis ist byte-gleich reproduzierbar -- der
Zeitstempel im ZIP ist fest.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape

COLUMNS = [
    "WORKDATE",
    "EMPLOYEE",
    "WBS_ELEMENT",
    "ABS_ATT_TYPE",
    "REC_ORDER",
    "ACTIVITY",
    "WAGETYPE",
    "CATSHOURS",
    "ZZICTPC",
    "SHORTTEXT",
]

HEADER_ROWS = 3          # das Muster wiederholt den Kopf dreimal
DATE_COLUMN = 1          # A
EMPLOYEE_COLUMN = 2      # B
WBS_COLUMN = 3           # C
HOURS_COLUMN = 8         # H

# Spaltenbreiten aus dem Muster
COLUMN_WIDTHS = {1: 11.18, 2: 11.45, 3: 24.82, 4: 12.54, 5: 12.54,
                 6: 11.27, 7: 11.27, 9: 15.73}

# Excel zaehlt Tage ab dem 31.12.1899 und rechnet 1900 faelschlich als
# Schaltjahr; fuer alle Daten ab 1900-03-01 stimmt dieser Bezugstag.
EXCEL_EPOCH = date(1899, 12, 30)

# Zellformate, in dieser Reihenfolge in styles.xml hinterlegt.
STYLE_NONE = 0
STYLE_DATE = 1           # numFmtId 14, im Muster "mm-dd-yy"
STYLE_HOURS = 2          # numFmtId 2, also 0.00

# Fester ZIP-Zeitstempel: derselbe Export ergibt dieselben Bytes.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_NS = "http://schemas.openxmlformats.org/officeDocument/2006"
PKG_NS = "http://schemas.openxmlformats.org/package/2006"


@dataclass(frozen=True)
class ExportRow:
    work_date: date
    employee: str          # Personalnummer, fuehrende Nullen erlaubt
    wbs_element: str
    hours: float


def _employee_number(raw: str) -> int | str:
    """'00123456' -> 123456. Nicht-numerische Werte bleiben unveraendert."""
    stripped = str(raw).strip()
    return int(stripped) if stripped.isdigit() else stripped


def _column_letter(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    letters = ""
    while index > 0:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _serial(day: date) -> int:
    """Datum als Excel-Serientag."""
    return (day - EXCEL_EPOCH).days


def _number(value: float | int) -> str:
    """Zahl fuer <v>: 4.0 wird zu '4', 5.55 bleibt '5.55'."""
    if isinstance(value, int):
        return str(value)
    return f"{round(value, 2):g}"


class _SharedStrings:
    """Die Tabelle, aus der SAP die Texte liest.

    Jeder Text bekommt einen Index; die Zelle verweist nur darauf. Genau daran
    scheiterte die openpyxl-Fassung: sie schrieb den Text in die Zelle selbst.
    """

    def __init__(self) -> None:
        self._index: dict[str, int] = {}
        self.uses = 0

    def ref(self, text: str) -> int:
        self.uses += 1
        return self._index.setdefault(text, len(self._index))

    def to_xml(self) -> str:
        items = "".join(f"<si><t>{escape(t)}</t></si>" for t in self._index)
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<sst xmlns="{MAIN_NS}" count="{self.uses}" '
            f'uniqueCount="{len(self._index)}">{items}</sst>'
        )


def _text_cell(ref: str, index: int) -> str:
    return f'<c r="{ref}" t="s"><v>{index}</v></c>'


def _number_cell(ref: str, value: str, style: int = STYLE_NONE) -> str:
    attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}"{attr}><v>{value}</v></c>'


def _sheet_xml(rows: list[ExportRow], strings: _SharedStrings) -> str:
    """Das Arbeitsblatt. Leere Zellen werden weggelassen, wie im Muster."""
    lines: list[str] = []
    span = f'spans="1:{len(COLUMNS)}"'

    for row_no in range(1, HEADER_ROWS + 1):
        cells = "".join(
            _text_cell(f"{_column_letter(col)}{row_no}", strings.ref(name))
            for col, name in enumerate(COLUMNS, start=1)
        )
        lines.append(f'<row r="{row_no}" {span}>{cells}</row>')

    for offset, row in enumerate(rows):
        row_no = HEADER_ROWS + 1 + offset
        employee = _employee_number(row.employee)
        cells = [
            _number_cell(f"A{row_no}", _number(_serial(row.work_date)), STYLE_DATE),
            _number_cell(f"B{row_no}", _number(employee))
            if isinstance(employee, int)
            else _text_cell(f"B{row_no}", strings.ref(employee)),
            _text_cell(f"C{row_no}", strings.ref(row.wbs_element)),
            _number_cell(f"H{row_no}", _number(float(row.hours)), STYLE_HOURS),
        ]
        lines.append(f'<row r="{row_no}" {span}>{"".join(cells)}</row>')

    last_row = HEADER_ROWS + len(rows)
    cols = "".join(
        f'<col min="{col}" max="{col}" width="{width}" customWidth="1"/>'
        for col, width in sorted(COLUMN_WIDTHS.items())
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{MAIN_NS}" xmlns:r="{DOC_NS}/relationships">'
        f'<dimension ref="A1:{_column_letter(len(COLUMNS))}{last_row}"/>'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<cols>{cols}</cols>'
        f'<sheetData>{"".join(lines)}</sheetData>'
        '</worksheet>'
    )


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Types xmlns="{PKG_NS}/content-types">'
    '<Default Extension="rels" '
    f'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.'
    'openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.extended-properties+xml"/>'
    '</Types>'
)

ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{PKG_NS}/relationships">'
    f'<Relationship Id="rId1" Type="{DOC_NS}/relationships/officeDocument" '
    'Target="xl/workbook.xml"/>'
    f'<Relationship Id="rId2" Type="{PKG_NS}/relationships/metadata/'
    'core-properties" Target="docProps/core.xml"/>'
    f'<Relationship Id="rId3" Type="{DOC_NS}/relationships/extended-properties" '
    'Target="docProps/app.xml"/>'
    '</Relationships>'
)

WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<workbook xmlns="{MAIN_NS}" xmlns:r="{DOC_NS}/relationships">'
    '<workbookPr/>'
    '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
    '<calcPr calcId="0"/>'
    '</workbook>'
)

WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Relationships xmlns="{PKG_NS}/relationships">'
    f'<Relationship Id="rId1" Type="{DOC_NS}/relationships/worksheet" '
    'Target="worksheets/sheet1.xml"/>'
    f'<Relationship Id="rId2" Type="{DOC_NS}/relationships/styles" '
    'Target="styles.xml"/>'
    f'<Relationship Id="rId3" Type="{DOC_NS}/relationships/sharedStrings" '
    'Target="sharedStrings.xml"/>'
    '</Relationships>'
)

# Drei Zellformate, in der Reihenfolge der STYLE_-Konstanten. Fill 0 und 1 sind
# vorgeschrieben, auch wenn nichts eingefaerbt wird.
STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<styleSheet xmlns="{MAIN_NS}">'
    '<fonts count="1"><font><sz val="11"/><name val="Calibri"/>'
    '<family val="2"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/>'
    '</border></borders>'
    '<cellStyleXfs count="1">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="3">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" '
    'applyNumberFormat="1"/>'
    '<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" '
    'applyNumberFormat="1"/>'
    '</cellXfs>'
    '<cellStyles count="1">'
    '<cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '<dxfs count="0"/>'
    '<tableStyles count="0" defaultTableStyle="TableStyleMedium9"/>'
    '</styleSheet>'
)

CORE_PROPS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<cp:coreProperties xmlns:cp="{PKG_NS}/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/">'
    '<dc:creator>fckcats</dc:creator>'
    '</cp:coreProperties>'
)

APP_PROPS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<Properties xmlns="{DOC_NS}/extended-properties">'
    '<Application>fckcats</Application>'
    '</Properties>'
)


def build_bytes(rows: list[ExportRow]) -> bytes:
    """Die fertige XLSX.

    Die Reihenfolge der Teile ist nicht beliebig: ``[Content_Types].xml`` muss
    als erster Eintrag im Archiv stehen, sonst weisen strenge OPC-Leser die
    Datei ab, ohne hineinzusehen.
    """
    strings = _SharedStrings()
    sheet = _sheet_xml(
        sorted(rows, key=lambda r: (r.work_date, r.wbs_element)), strings
    )

    parts = [
        ("[Content_Types].xml", CONTENT_TYPES),
        ("_rels/.rels", ROOT_RELS),
        ("xl/workbook.xml", WORKBOOK),
        ("xl/_rels/workbook.xml.rels", WORKBOOK_RELS),
        ("xl/worksheets/sheet1.xml", sheet),
        ("xl/styles.xml", STYLES),
        # Erst jetzt vollstaendig: das Blatt hat die Tabelle gefuellt.
        ("xl/sharedStrings.xml", strings.to_xml()),
        ("docProps/core.xml", CORE_PROPS),
        ("docProps/app.xml", APP_PROPS),
    ]

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts:
            info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode("utf-8"))
    return buf.getvalue()


def suggest_filename(date_from: date, date_to: date) -> str:
    return f"CATS_{date_from:%Y%m%d}_{date_to:%Y%m%d}.xlsx"
