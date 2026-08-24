"""Verteilung der validierten Arbeitsstunden auf die WBS-Elemente.

Zwei Regeln mit klarer Rangfolge (SPEC.md §4):

  1. HART   Die Tagessumme der erzeugten Zeilen entspricht exakt der
            validierten Stundenzahl des Tages. Wird nie gerundet oder gekuerzt.
  2. WEICH  Die Gewichtung soll je ISO-Woche moeglichst gut getroffen werden.
            Exakt geht nicht, weil die Tagesstunden krumm sind.

Die Slices werden bewusst grob gehalten (4 h vor 2 h vor 1 h); der krumme
Tagesrest landet in einem einzigen Slice.

Zwei Arten von WBS-Elementen teilen sich die Woche:

  * **Projekte** tragen eine Obergrenze in Stunden je Woche. Sie werden zuerst
    bedient und anteilig zur Wochenlaenge gekuerzt -- eine Woche mit zwei von
    fuenf Arbeitstagen erhaelt 40 Prozent der Obergrenze.
  * **Operations** tragen Prozente und teilen unter sich auf, was nach den
    Projekten uebrig bleibt.

Welche Seite bei Knappheit nachgibt, entscheidet der Vorrang: bei Vorrang fuer
die Projekte kann Operations leer ausgehen, bei Vorrang fuer Operations bleibt
ihm ein Mindestanteil der Woche erhalten.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

LADDER = [4.0, 2.0, 1.0]
MAX_SLICES_PER_DAY = 4
# Angestrebte Untergrenze: moeglichst keine Zeile unter einer Stunde.
MIN_SLICE_HOURS = 1.0
# Harte Untergrenze. CATS nimmt kleinere Buchungen nicht an, deshalb darf
# keine Zeile darunter liegen -- auch nicht der krumme Tagesrest. Die
# Zuordnung wird dadurch etwas groeber, dafuer ist die Datei einspielbar.
MIN_BOOKABLE_HOURS = 0.5
# Bezugsgroesse fuer die anteilige Kuerzung der Projekt- und Verwaltungsstunden.
FULL_WEEK_DAYS = 5

# Platzhalter fuer Zeit, die keinem WBS-Element zugeordnet und nicht gebucht
# wird -- etwa Verwaltungstaetigkeit. Sie belegt bei der Verteilung Stunden wie
# ein Projekt, faellt aber vor dem Erzeugen der Zeilen wieder heraus.
UNBOOKED = "\x00unbooked"
# Wie viele Zufallsvarianten durchgerechnet werden, bevor die beste gewinnt.
DEFAULT_CANDIDATES = 200


@dataclass(frozen=True)
class Allocation:
    work_date: date
    wbs_element: str
    hours: float


@dataclass
class Plan:
    """Was in einer Woche verteilt werden soll.

    ops       [(wbs, gewicht 0..1)] -- teilen sich, was nach den Projekten bleibt
    projects  [(wbs, obergrenze in stunden je voller Woche)]
    priority  'projects' oder 'operations'
    ops_min_pct  Mindestanteil der Woche fuer Operations; greift nur bei
                 Vorrang fuer Operations
    unbooked_hours_per_week
                 Stunden je voller Woche, die keinem WBS-Element zugeordnet
                 und nicht exportiert werden
    """
    ops: list[tuple[str, float]] = field(default_factory=list)
    projects: list[tuple[str, float]] = field(default_factory=list)
    priority: str = "projects"
    ops_min_pct: float = 0.0
    # Stunden je voller Woche, die nicht gebucht werden.
    unbooked_hours_per_week: float = 0.0


@dataclass
class WeekReport:
    """Soll/Ist je Woche -- wird im UI angezeigt, damit sichtbar ist, wo es klemmt."""
    iso_year: int
    iso_week: int
    days: int
    hours: float
    per_wbs: dict[str, float]
    target_per_wbs: dict[str, float]
    project_hours: float = 0.0        # tatsaechlich an Projekte vergeben
    ops_hours: float = 0.0            # tatsaechlich an Operations vergeben
    unbooked_hours: float = 0.0       # bewusst nicht gebucht
    projects_capped: bool = False     # Obergrenzen mussten gekuerzt werden
    ops_starved: bool = False         # Operations ging leer aus

    @property
    def bookable_hours(self) -> float:
        """Wochenstunden abzueglich der bewusst nicht gebuchten."""
        return round(self.hours - self.unbooked_hours, 2)

    @property
    def max_deviation_pp(self) -> float:
        """Groesste Abweichung in Prozentpunkten.

        Bezugsgroesse sind die gebuchten Stunden -- die nicht gebuchte Zeit
        gehoert keinem WBS-Element und wuerde die Anteile sonst verzerren.
        """
        base = self.bookable_hours
        if base <= 0:
            return 0.0
        return max(
            (abs(self.per_wbs.get(k, 0.0) - v) / base * 100
             for k, v in self.target_per_wbs.items()),
            default=0.0,
        )


def compute_targets(
    hours_total: float,
    day_count: int,
    plan: Plan,
    carry: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict]:
    """Rechnet die Wochenziele je WBS-Element aus.

    Zuerst die Projekte: ihre Obergrenze wird anteilig zur Wochenlaenge
    gekuerzt, damit eine Randwoche mit zwei Arbeitstagen nicht denselben
    Projektblock traegt wie eine volle. Was danach uebrig bleibt, teilen die
    Operations-Elemente nach ihren Gewichten unter sich auf.

    Der Uebertrag aus der Vorwoche gilt nur fuer Operations. Bei den Projekten
    ist die Angabe eine Obergrenze und kein Soll -- eine Woche, in der weniger
    anfiel, darf die naechste nicht aufblaehen.
    """
    carry = carry or {}
    meta = {"project_hours": 0.0, "ops_hours": 0.0, "unbooked_hours": 0.0,
            "projects_capped": False, "ops_starved": False}
    if hours_total <= 0:
        return {}, meta

    # Anteil dieser Woche an einer vollen Arbeitswoche.
    share = min(1.0, day_count / FULL_WEEK_DAYS) if day_count else 0.0

    # Zuerst die Zeit abziehen, die gar nicht gebucht wird. Sie steht keinem
    # WBS-Element zur Verfuegung, also darf sie auch nicht in die Verteilung
    # eingehen -- weder bei den Projekten noch bei der Gewichtung.
    unbooked = min(
        round(plan.unbooked_hours_per_week * share, 4),
        hours_total,
    ) if plan.unbooked_hours_per_week > 0 else 0.0
    # Ein Bruchteil unter der Mindestbuchung laesst sich nicht sauber
    # aussparen; dann wird die Zeit eben gebucht.
    if unbooked < MIN_BOOKABLE_HOURS:
        unbooked = 0.0
    distributable = round(hours_total - unbooked, 4)
    meta["unbooked_hours"] = round(unbooked, 2)

    if distributable <= 0.005:
        # Die ganze Woche geht in nicht gebuchte Zeit.
        return ({UNBOOKED: round(hours_total, 4)} if unbooked > 0 else {}), meta

    wanted = {wbs: max_hours * share for wbs, max_hours in plan.projects}
    total_wanted = sum(wanted.values())

    # Wie viel duerfen die Projekte hoechstens belegen?
    if plan.ops and plan.priority == "operations" and plan.ops_min_pct > 0:
        budget = distributable * (1.0 - plan.ops_min_pct / 100.0)
    else:
        budget = distributable
    # Ohne Operations-Elemente duerfen die Projekte alles Verteilbare fuellen.
    if not plan.ops:
        budget = distributable

    if total_wanted > budget + 0.005 and total_wanted > 0:
        # Anteilig kuerzen, im Verhaeltnis der Obergrenzen zueinander.
        factor = budget / total_wanted
        wanted = {k: v * factor for k, v in wanted.items()}
        meta["projects_capped"] = True

    # Projektanteile unter der Mindestbuchung entfallen; ihre Stunden gehen an
    # die gewichtete Verteilung, statt eine nicht buchbare Zeile zu erzeugen.
    targets = {k: round(v, 4) for k, v in wanted.items() if v >= MIN_BOOKABLE_HOURS}
    project_hours = sum(targets.values())
    rest = round(distributable - project_hours, 4)
    meta["project_hours"] = round(project_hours, 2)
    if unbooked > 0.005:
        targets[UNBOOKED] = round(unbooked, 4)

    if plan.ops and rest > 0.005:
        weight_sum = sum(w for _, w in plan.ops) or 1.0
        for wbs, weight in plan.ops:
            targets[wbs] = round(rest * weight / weight_sum + carry.get(wbs, 0.0), 4)
        meta["ops_hours"] = round(rest, 2)
    elif plan.ops:
        meta["ops_starved"] = True

    return targets, meta


def plan_week(
    days: list[tuple[date, float]],
    targets: dict[str, float],
    rng: random.Random,
) -> tuple[list[Allocation], float]:
    """Verteilt die Stunden einer ISO-Woche auf vorgegebene Wochenziele.

    days    [(datum, stunden)] der buchbaren Tage dieser Woche
    targets {wbs_element: sollstunden fuer diese Woche}

    Rueckgabe: (zeilen, wochenstunden)
    """
    hours_total = round(sum(h for _, h in days), 2)
    if hours_total <= 0 or not targets:
        return [], 0.0

    need = {k: round(v, 4) for k, v in targets.items()}

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
                remainder = round(rest - block, 2)
                # Harte Grenze: nichts unter der Mindestbuchung stehen lassen.
                # Betrifft vor allem den krummen Tagesanteil -- 8,05 h ergaeben
                # sonst eine Zeile mit 0,05 h, die CATS ablehnt.
                if 0.005 < remainder < MIN_BOOKABLE_HOURS:
                    block = rest
                # Darueber hinaus moeglichst keine Reste unter einer Stunde,
                # ausser es ist der krumme Tagesanteil selbst.
                elif 0.005 < remainder < MIN_SLICE_HOURS and abs(remainder - frac) > 0.005:
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
    return allocations, hours_total


def plan_range(
    days: list[tuple[date, float]],
    plan: Plan,
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
    # Uebertrag nur fuer Operations -- bei Projekten ist die Angabe eine
    # Obergrenze, kein Soll, das sich ansammeln duerfte.
    carry: dict[str, float] = {}
    ops_names = {wbs for wbs, _ in plan.ops}

    for (iso_year, iso_week) in sorted(weeks):
        week_days = weeks[(iso_year, iso_week)]
        hours_total = round(sum(h for _, h in week_days), 2)
        targets, meta = compute_targets(hours_total, len(week_days), plan, carry)
        rows, _ = plan_week(week_days, targets, rng)

        # Die Platzhalter-Zeilen fallen hier heraus: sie haben ihre Stunden bei
        # der Verteilung gebunden, gehoeren aber keinem WBS-Element und duerfen
        # nicht exportiert werden.
        booked = [a for a in rows if a.wbs_element != UNBOOKED]
        unbooked_hours = round(
            sum(a.hours for a in rows if a.wbs_element == UNBOOKED), 2)
        allocations.extend(booked)

        per_wbs: dict[str, float] = {}
        for a in booked:
            per_wbs[a.wbs_element] = round(per_wbs.get(a.wbs_element, 0.0) + a.hours, 2)

        # Was Operations diese Woche nicht bekommen hat, geht in die naechste.
        carry = {
            wbs: round(targets.get(wbs, 0.0) - per_wbs.get(wbs, 0.0), 2)
            for wbs in ops_names
        }

        reports.append(WeekReport(
            iso_year=iso_year,
            iso_week=iso_week,
            days=len(week_days),
            hours=hours_total,
            per_wbs=per_wbs,
            target_per_wbs={k: round(v, 2) for k, v in targets.items()
                            if k != UNBOOKED},
            unbooked_hours=unbooked_hours,
            project_hours=round(
                sum(h for w, h in per_wbs.items() if w not in ops_names), 2),
            ops_hours=round(
                sum(h for w, h in per_wbs.items() if w in ops_names), 2),
            projects_capped=meta["projects_capped"],
            ops_starved=meta["ops_starved"],
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
    plan: Plan,
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
        allocations, reports = plan_range(days, plan, candidate_seed)
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
