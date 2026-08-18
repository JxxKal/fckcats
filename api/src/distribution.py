"""Verteilung der validierten Arbeitsstunden auf die WBS-Elemente.

Zwei Regeln mit klarer Rangfolge (SPEC.md §4):

  1. HART   Die Tagessumme der erzeugten Zeilen entspricht exakt der
            validierten Stundenzahl des Tages. Wird nie gerundet oder gekuerzt.
  2. WEICH  Die Gewichtung soll je ISO-Woche moeglichst gut getroffen werden.
            Exakt geht nicht, weil die Tagesstunden krumm sind.

Die Slices werden bewusst grob gehalten (4 h vor 2 h vor 1 h); der krumme
Tagesrest landet in einem einzigen Slice.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date

LADDER = [4.0, 2.0, 1.0]
MAX_SLICES_PER_DAY = 4
MIN_SLICE_HOURS = 1.0
# Wie viele Zufallsvarianten durchgerechnet werden, bevor die beste gewinnt.
DEFAULT_CANDIDATES = 200


@dataclass(frozen=True)
class Allocation:
    work_date: date
    wbs_element: str
    hours: float


@dataclass
class WeekReport:
    """Soll/Ist je Woche -- wird im UI angezeigt, damit sichtbar ist, wo es klemmt."""
    iso_year: int
    iso_week: int
    days: int
    hours: float
    per_wbs: dict[str, float]
    target_per_wbs: dict[str, float]

    @property
    def max_deviation_pp(self) -> float:
        """Groesste Abweichung in Prozentpunkten."""
        if not self.hours:
            return 0.0
        return max(
            (abs(self.per_wbs.get(k, 0.0) - v) / self.hours * 100 for k, v in self.target_per_wbs.items()),
            default=0.0,
        )


def plan_week(
    days: list[tuple[date, float]],
    weights: list[tuple[str, float]],
    carry: dict[str, float],
    rng: random.Random,
) -> tuple[list[Allocation], float, dict[str, float]]:
    """Verteilt die Stunden einer ISO-Woche.

    days    [(datum, stunden)] der buchbaren Tage dieser Woche
    weights [(wbs_element, gewicht 0..1)], Summe der Gewichte == 1.0
    carry   Uebertrag aus der Vorwoche je WBS-Element in Stunden (kann negativ sein)

    Rueckgabe: (zeilen, wochenstunden, uebertrag_fuer_die_folgewoche)
    """
    hours_total = round(sum(h for _, h in days), 2)
    if hours_total <= 0 or not weights:
        return [], 0.0, dict(carry)

    # Wochensoll inklusive Uebertrag -- so gleichen sich Randwochen mit nur
    # ein bis zwei Tagen ueber den Zeitraum wieder aus.
    need = {k: round(hours_total * w + carry.get(k, 0.0), 4) for k, w in weights}

    rows: list[tuple[date, str, float]] = []
    # Zufaellige Tagesreihenfolge -> jede Woche sieht anders aus.
    for day, hours in sorted(days, key=lambda x: rng.random()):
        rest = round(hours, 2)
        frac = round(hours - int(hours), 2)     # krummer Tagesanteil, z.B. 0.82
        used: set[str] = set()
        slice_no = 0

        while rest > 0.005:
            slice_no += 1
            # Kein WBS-Element zweimal am selben Tag.
            candidates = [k for k in need if k not in used] or list(need)
            # Groesster offener Restbedarf gewinnt, Gleichstand zufaellig.
            k = max(candidates, key=lambda k: (need[k], rng.random()))

            if slice_no >= MAX_SLICES_PER_DAY:
                block = rest                    # letzter Slice nimmt alles
            else:
                block = next(
                    (s for s in LADDER if s <= rest and s <= need[k] + 0.5),
                    None,
                )
                if block is None:
                    block = rest
                # Keinen Kruemel hinterlassen: was uebrig bleibt, muss 0 sein,
                # mindestens MIN_SLICE_HOURS betragen oder genau der krumme
                # Tagesanteil sein.
                remainder = round(rest - block, 2)
                if 0.005 < remainder < MIN_SLICE_HOURS and abs(remainder - frac) > 0.005:
                    block = rest

            block = round(block, 2)
            rows.append((day, k, block))
            need[k] = round(need[k] - block, 4)
            used.add(k)
            rest = round(rest - block, 2)

    # Gleiches WBS am selben Tag zusammenfassen; kann durch den Rest-Slice auftreten.
    merged: dict[tuple[date, str], float] = {}
    for day, k, block in rows:
        merged[(day, k)] = round(merged.get((day, k), 0.0) + block, 2)

    allocations = [Allocation(d, k, h) for (d, k), h in sorted(merged.items())]
    return allocations, hours_total, {k: round(v, 2) for k, v in need.items()}


def plan_range(
    days: list[tuple[date, float]],
    weights: list[tuple[str, float]],
    seed: int,
) -> tuple[list[Allocation], list[WeekReport]]:
    """Verteilt einen Zeitraum, Woche fuer Woche, mit Uebertrag.

    Der Seed wird beim Berechnungslauf gespeichert, damit ein erneuter Export
    desselben Zeitraums identische Zeilen liefert.
    """
    rng = random.Random(seed)
    weeks: dict[tuple[int, int], list[tuple[date, float]]] = {}
    for day, hours in days:
        iso = day.isocalendar()
        weeks.setdefault((iso[0], iso[1]), []).append((day, hours))

    allocations: list[Allocation] = []
    reports: list[WeekReport] = []
    carry: dict[str, float] = {}

    for (iso_year, iso_week) in sorted(weeks):
        week_days = weeks[(iso_year, iso_week)]
        rows, hours_total, carry = plan_week(week_days, weights, carry, rng)
        allocations.extend(rows)

        per_wbs: dict[str, float] = {}
        for a in rows:
            per_wbs[a.wbs_element] = round(per_wbs.get(a.wbs_element, 0.0) + a.hours, 2)
        reports.append(WeekReport(
            iso_year=iso_year,
            iso_week=iso_week,
            days=len(week_days),
            hours=hours_total,
            per_wbs=per_wbs,
            target_per_wbs={k: round(hours_total * w, 2) for k, w in weights},
        ))

    return allocations, reports


def score_reports(reports: list[WeekReport]) -> float:
    """Bewertet eine Verteilung. Kleiner ist besser.

    Summe der quadrierten Wochenabweichungen: Ausreisser wiegen schwer, aber
    eine unvermeidbar grobe Randwoche blockiert nicht die Optimierung der
    uebrigen Wochen -- was passierte, als nur das Maximum zaehlte.
    """
    return sum(r.max_deviation_pp ** 2 for r in reports)


def plan_range_best_of(
    days: list[tuple[date, float]],
    weights: list[tuple[str, float]],
    seed: int,
    candidates: int = DEFAULT_CANDIDATES,
) -> tuple[list[Allocation], list[WeekReport], int]:
    """Rechnet mehrere Verteilungen durch und nimmt die wochenscharf beste.

    Die Verteilung ist zufaellig, entsprechend schwankt ihre Qualitaet je nach
    Seed deutlich. Ein paar Dutzend Kandidaten durchzurechnen kostet
    Millisekunden und druckt die Abweichung sichtbar. Der Gewinner-Seed wird
    zurueckgegeben und gespeichert, damit das Ergebnis reproduzierbar bleibt.
    """
    best: tuple[list[Allocation], list[WeekReport], int] | None = None
    best_score: float | None = None

    for i in range(max(1, candidates)):
        candidate_seed = seed + i
        allocations, reports = plan_range(days, weights, candidate_seed)
        current = score_reports(reports)
        if best_score is None or current < best_score:
            best, best_score = (allocations, reports, candidate_seed), current

    assert best is not None
    return best


def minimum_weight_pct(week_hours: float) -> float:
    """Kleinste Gewichtung, die bei dieser Wochenarbeitszeit noch buchbar ist.

    Grundlage der Plausibilitaetswarnung in der CATS-Config: faellt der
    Wochenanteil eines WBS-Elements unter MIN_SLICE_HOURS, kann es in einer
    typischen Woche nicht sinnvoll gebucht werden.
    """
    if week_hours <= 0:
        return 0.0
    return round(MIN_SLICE_HOURS / week_hours * 100, 1)
