"""Parser fuer Zeitnachweis-PDFs.

Extraktion per `pdftotext -layout`. Das Layout bleibt dabei als Text erhalten,
sodass sich die Tageszeilen zuverlaessig lesen lassen:

    Tag        von     bis     Std.    Sollz   Verfall  GLZ    Mehrz   Prod
    01 MI      08:00   16:30    8,50    7,50            0,50   0,00    8,00
    04 SA  Frei laut AZP
    17 FR  Urlaub     08:30   16:00    7,50    7,50            0,00   0,00    0,00

Buchungsbasis ist die Spalte `Prod.` (Nettoarbeitszeit) und nicht `Std.`
(Brutto-Anwesenheit inklusive Pause) -- siehe SPEC.md §2.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date

# Abwesenheitsarten, die nie als Arbeitszeit gebucht werden.
# `*` am Ende bedeutet Praefix-Match. Vergleich case-insensitiv.
EXCLUDED_REASONS = [
    "frei laut azp",
    "urlaub",
    "au",
    "au ohne attest",
    "au mit attest",
    "arbeitsunfähig*",
    "reisezeit",
    "sonderurlaub",
    "zeitausgleich*",
]

WEEKDAYS = {"MO", "DI", "MI", "DO", "FR", "SA", "SO"}

MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}

# "01 MI   <grund?>   <rest>"
_DAY_RE = re.compile(r"^\s*(\d{1,2})\s+(MO|DI|MI|DO|FR|SA|SO)\b(.*)$")
# "08:10    16:30"
_TIMES_RE = re.compile(r"(\d{1,2}:\d{2})\s+(\d{1,2}:\d{2})")
# "8,33" oder "0,94-" (nachgestelltes Minus)
_NUM_RE = re.compile(r"(\d+(?:,\d+)?)(-?)")
_HEADER_RE = re.compile(r"^\s*(\d{6,10})\s+(\S.*?)\s*$")
_MONTH_RE = re.compile(r"Monat:\s*([A-Za-zäöüÄÖÜ]+)\s*-\s*(\d{4})")
# Uhrzeit oder Dezimalzahl -- markiert den Beginn des Datenteils einer Zeile
_VALUE_RE = re.compile(r"\d{1,2}:\d{2}|\d+(?:,\d+)?-?")

# Spaltenkopf der Tagestabelle
_TABLE_HEADER_COLS = ["von", "bis", "Std.", "Sollz", "Verfall", "GLZ", "Mehrz", "Prod"]
# Werte stehen 1-3 Zeichen rechts vom Spaltenkopf, der naechste Kopf ist >= 9
# entfernt. 6 trennt beides zuverlaessig.
_COL_TOLERANCE = 6


def _find_columns(text: str) -> dict[str, int]:
    """Liest die Zeichenpositionen der Spaltenkoepfe aus der Tabellenueberschrift.

    Damit lassen sich Werte spaltengenau zuordnen, auch wenn einzelne Spalten
    leer bleiben (Verfall ist das meistens).
    """
    for line in text.splitlines():
        if "Sollz" in line and "Std." in line and "von" in line:
            cols = {}
            for name in _TABLE_HEADER_COLS:
                idx = line.find(name)
                if idx >= 0:
                    cols[name] = idx
            return cols
    return {}


def _assign_by_column(line: str, cols: dict[str, int]) -> dict[str, str]:
    """Ordnet die Werte einer Zeile den Spalten nach Zeichenposition zu."""
    out: dict[str, str] = {}
    for m in _VALUE_RE.finditer(line):
        best, best_dist = None, _COL_TOLERANCE
        for name, start in cols.items():
            dist = abs(m.start() - start)
            if dist < best_dist:
                best, best_dist = name, dist
        if best and best not in out:
            out[best] = m.group(0)
    return out


@dataclass
class ParsedDay:
    """Eine Tageszeile aus dem PDF."""
    day: int
    weekday: str
    reason: str = ""
    time_from: str | None = None
    time_to: str | None = None
    hours_gross: float | None = None      # Spalte Std.
    hours_target: float | None = None     # Spalte Sollz
    hours_net: float | None = None        # Spalte Prod. -- die Buchungsbasis
    work_date: date | None = None

    # Auswertung
    excluded: bool = False                # steht auf der Ausschlussliste
    unknown_reason: bool = False          # Grundtext, der nicht bekannt ist
    incomplete: bool = False              # Werktag ohne Grund und ohne Stunden

    @property
    def bookable(self) -> bool:
        return (
            not self.excluded
            and not self.unknown_reason
            and not self.incomplete
            and bool(self.hours_net)
        )


@dataclass
class ParsedSheet:
    personnel_number: str | None = None
    employee_name: str | None = None
    month: int | None = None
    year: int | None = None
    days: list[ParsedDay] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def bookable_days(self) -> list[ParsedDay]:
        return [d for d in self.days if d.bookable]

    @property
    def clarifications(self) -> list[ParsedDay]:
        """Tage, zu denen der User eine Entscheidung treffen muss."""
        return [d for d in self.days if d.unknown_reason or d.incomplete]


_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def normalize_reason(reason: str) -> str:
    """Kleinschreibung + aufgeloeste Umlaute + normalisierte Leerzeichen.

    pdftotext liefert Umlaute je nach Font-Encoding mal als 'ä', mal als 'ae'.
    Beide Schreibweisen muessen dieselbe Regel treffen, sonst rutscht ein
    Krankheitstag als Arbeitszeit durch.
    """
    return " ".join(reason.strip().lower().translate(_UMLAUTS).split())


def is_excluded_reason(reason: str) -> bool:
    """Prueft den Grundtext gegen die Ausschlussliste (case-insensitiv, `*` = Praefix)."""
    r = normalize_reason(reason)
    if not r:
        return False
    for raw in EXCLUDED_REASONS:
        is_prefix = raw.endswith("*")
        pattern = normalize_reason(raw.rstrip("*"))
        if is_prefix:
            if r.startswith(pattern):
                return True
        elif r == pattern:
            return True
    return False


def pdf_to_text(path: str) -> str:
    """Ruft pdftotext -layout auf. Wirft RuntimeError, wenn das fehlschlaegt."""
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True,
            timeout=60,
            check=True,
        )
    except FileNotFoundError:
        raise RuntimeError("pdftotext ist nicht installiert (Paket poppler-utils).")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"pdftotext ist fehlgeschlagen: {e.stderr.decode(errors='replace')}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("pdftotext hat das Zeitlimit ueberschritten.")
    return result.stdout.decode("utf-8", errors="replace")


def _parse_number(token: str) -> float:
    """'8,33' -> 8.33 ; '0,94-' -> -0.94 (nachgestelltes Minus)."""
    token = token.strip()
    negative = token.endswith("-")
    token = token.rstrip("-").replace(",", ".")
    value = float(token)
    return -value if negative else value


def _extract_numbers(text: str) -> list[float]:
    return [_parse_number(m.group(0)) for m in _NUM_RE.finditer(text) if m.group(1)]


def parse_text(text: str, reason_rules: dict[str, str] | None = None) -> ParsedSheet:
    """Wertet die Textausgabe von pdftotext aus.

    reason_rules: fruehere Entscheidungen des Users zu unbekannten Grundtexten,
                  {"Dienstreise": "book" | "exclude"}. Bekannte Texte erzeugen
                  dann keinen Klaerfall mehr.
    """
    rules = {normalize_reason(k): v for k, v in (reason_rules or {}).items()}
    sheet = ParsedSheet()
    cols = _find_columns(text)
    if not cols:
        sheet.warnings.append(
            "Spaltenkopf der Tagestabelle nicht gefunden - Werte werden der Reihe nach zugeordnet."
        )

    for line in text.splitlines():
        if sheet.month is None:
            m = _MONTH_RE.search(line)
            if m:
                sheet.month = MONTHS.get(m.group(1).strip().lower())
                sheet.year = int(m.group(2))
                if sheet.month is None:
                    sheet.warnings.append(f"Unbekannter Monatsname: {m.group(1)}")
                continue

        if sheet.personnel_number is None:
            m = _HEADER_RE.match(line)
            # Der Kopf steht vor der Tagestabelle und enthaelt keine Uhrzeiten.
            if m and ":" not in line and not line.strip().startswith("0" * 0 + "Monat"):
                sheet.personnel_number = m.group(1)
                sheet.employee_name = m.group(2)
                continue

        m = _DAY_RE.match(line)
        if not m:
            continue

        day_num, weekday, rest = int(m.group(1)), m.group(2), m.group(3)
        parsed = ParsedDay(day=day_num, weekday=weekday)

        if cols:
            # Spaltengenaue Zuordnung ueber die Zeichenposition. Noetig, weil
            # Verfall meist leer ist und einzelne Spalten fehlen koennen, wenn
            # Kommen oder Gehen vergessen wurde.
            values = _assign_by_column(line, cols)
            parsed.time_from = values.get("von")
            parsed.time_to = values.get("bis")
            parsed.hours_gross = _parse_number(values["Std."]) if "Std." in values else None
            parsed.hours_target = _parse_number(values["Sollz"]) if "Sollz" in values else None
            parsed.hours_net = _parse_number(values["Prod"]) if "Prod" in values else None
            # Der Grundtext steht links der ersten Datenspalte.
            reason_end = min(cols.values())
            parsed.reason = line[:reason_end].strip()
            parsed.reason = _DAY_RE.match(parsed.reason).group(3).strip() \
                if _DAY_RE.match(parsed.reason) else ""
        else:
            # Fallback ohne Spaltenkopf: Grundtext endet vor dem ersten Wert.
            first_value = _VALUE_RE.search(rest)
            parsed.reason = (rest[: first_value.start()] if first_value else rest).strip()
            times = _TIMES_RE.search(rest)
            if times:
                parsed.time_from, parsed.time_to = times.group(1), times.group(2)
                numbers = _extract_numbers(rest[times.end():])
            else:
                numbers = _extract_numbers(rest)
            if len(numbers) >= 3:
                parsed.hours_gross = numbers[0]
                parsed.hours_target = numbers[1]
                parsed.hours_net = numbers[-1]
            elif len(numbers) == 1:
                # Nur die Sollzeit steht da -- typisch fuer einen Tag, an dem
                # Kommen oder Gehen vergessen wurde.
                parsed.hours_target = numbers[0]

        sheet.days.append(parsed)

    _classify(sheet, rules)
    _assign_dates(sheet)
    return sheet


def _classify(sheet: ParsedSheet, rules: dict[str, str]) -> None:
    for d in sheet.days:
        reason_key = normalize_reason(d.reason)

        if is_excluded_reason(d.reason):
            d.excluded = True
            continue

        if reason_key and reason_key in rules:
            # Der User hat diesen Grundtext bereits einmal entschieden.
            if rules[reason_key] == "exclude":
                d.excluded = True
            continue

        if reason_key:
            # Unbekannter Grundtext -> nicht stillschweigend buchen.
            d.unknown_reason = True
            continue

        # Kein Grund eingetragen: dann muessen Stunden und Zeiten da sein.
        if not d.hours_net or d.time_from is None or d.time_to is None:
            if d.weekday in ("SA", "SO"):
                # Wochenende ohne jeden Eintrag ist normal, kein Klaerfall.
                d.excluded = True
            else:
                d.incomplete = True


def _assign_dates(sheet: ParsedSheet) -> None:
    if sheet.month is None or sheet.year is None:
        sheet.warnings.append("Monat/Jahr konnten nicht gelesen werden.")
        return
    for d in sheet.days:
        try:
            d.work_date = date(sheet.year, sheet.month, d.day)
        except ValueError:
            sheet.warnings.append(f"Ungueltiges Datum: {d.day:02d}.{sheet.month:02d}.{sheet.year}")


def parse_pdf(path: str, reason_rules: dict[str, str] | None = None) -> ParsedSheet:
    return parse_text(pdf_to_text(path), reason_rules)
