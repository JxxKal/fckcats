"""Tests fuer den Verteilalgorithmus.

Die harte Zusage lautet: die Tagessumme stimmt immer exakt. Die Gewichtung ist
ein Optimierungsziel mit Toleranz -- entsprechend pruefen die Tests die
Tagessumme scharf und die Gewichtung nur grob.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import distribution  # noqa: E402
from distribution import (  # noqa: E402
    Plan, compute_targets, minimum_weight_pct, plan_range, plan_range_best_of,
)

OPS = [
    ("DEO1111-NP/PJ00-O51.0000", 0.40),
    ("DEO2222-NP/PJ00-O51.0000", 0.25),
    ("DEO3333-NP/PJ00-O51.0000", 0.15),
    ("DEO4444-NP/PJ00-O51.0000", 0.15),
    ("DEO5555000/PQ00-A02.0000", 0.05),
]
WEIGHTS = Plan(ops=OPS)

# Zwei Randwochen und eine volle Woche, mit krummen Stunden.
DAYS = [
    (date(2026, 3, 5), 6.82), (date(2026, 3, 6), 5.86),
    (date(2026, 3, 9), 8.88), (date(2026, 3, 10), 6.00),
    (date(2026, 3, 11), 7.89), (date(2026, 3, 12), 7.52),
    (date(2026, 3, 13), 6.84),
    (date(2026, 3, 16), 7.55),
]


def day_sums(allocations):
    out = {}
    for a in allocations:
        out[a.work_date] = round(out.get(a.work_date, 0.0) + a.hours, 2)
    return out


def test_tagessumme_ist_exakt():
    allocations, _ = plan_range(DAYS, WEIGHTS, seed=42)
    sums = day_sums(allocations)
    for d, hours in DAYS:
        assert abs(sums[d] - hours) < 0.005, f"{d}: {sums[d]} != {hours}"


def test_gesamtsumme_ist_exakt():
    allocations, _ = plan_range(DAYS, WEIGHTS, seed=42)
    assert abs(sum(a.hours for a in allocations) - sum(h for _, h in DAYS)) < 0.005


def test_kein_wbs_element_doppelt_am_selben_tag():
    allocations, _ = plan_range(DAYS, WEIGHTS, seed=7)
    seen = set()
    for a in allocations:
        key = (a.work_date, a.wbs_element)
        assert key not in seen
        seen.add(key)


def test_hoechstens_vier_zeilen_pro_tag():
    allocations, _ = plan_range(DAYS, WEIGHTS, seed=7)
    per_day = {}
    for a in allocations:
        per_day[a.work_date] = per_day.get(a.work_date, 0) + 1
    assert max(per_day.values()) <= distribution.MAX_SLICES_PER_DAY


def test_gleicher_seed_liefert_identisches_ergebnis():
    a1, _ = plan_range(DAYS, WEIGHTS, seed=99)
    a2, _ = plan_range(DAYS, WEIGHTS, seed=99)
    assert a1 == a2


def test_anderer_seed_liefert_andere_verteilung():
    a1, _ = plan_range(DAYS, WEIGHTS, seed=1)
    a2, _ = plan_range(DAYS, WEIGHTS, seed=2)
    assert a1 != a2


def test_volle_woche_trifft_gewichtung_gut():
    # Nur die volle Fuenf-Tage-Woche; Randwochen sind naturgemaess grob.
    week = [(d, h) for d, h in DAYS if date(2026, 3, 9) <= d <= date(2026, 3, 13)]
    _, reports = plan_range(week, WEIGHTS, seed=42)
    assert reports[0].max_deviation_pp < 5.0


def test_uebertrag_gleicht_randwochen_ueber_den_zeitraum_aus():
    allocations, _ = plan_range(DAYS, WEIGHTS, seed=42)
    total = sum(a.hours for a in allocations)
    per_wbs = {}
    for a in allocations:
        per_wbs[a.wbs_element] = per_wbs.get(a.wbs_element, 0.0) + a.hours
    for wbs, weight in OPS:
        actual = per_wbs.get(wbs, 0.0) / total * 100
        assert abs(actual - weight * 100) < 6.0, f"{wbs}: {actual:.1f}% statt {weight*100}%"


def test_einzelner_tag_funktioniert():
    allocations, _ = plan_range([(date(2026, 3, 16), 7.55)], WEIGHTS, seed=3)
    assert abs(sum(a.hours for a in allocations) - 7.55) < 0.005


def test_leerer_zeitraum():
    allocations, reports = plan_range([], WEIGHTS, seed=1)
    assert allocations == [] and reports == []


def test_ein_einziges_wbs_element_bekommt_alles():
    allocations, _ = plan_range(DAYS, Plan(ops=[("NUR-EINS", 1.0)]), seed=5)
    assert {a.wbs_element for a in allocations} == {"NUR-EINS"}
    assert abs(sum(a.hours for a in allocations) - sum(h for _, h in DAYS)) < 0.005


def test_lange_zeitraeume_bleiben_exakt():
    # 26 Wochen am Stueck, jeder Werktag mit krummer Stundenzahl.
    days, d = [], date(2026, 1, 5)
    while d < date(2026, 7, 6):
        if d.weekday() < 5:
            days.append((d, round(6.0 + (d.toordinal() % 27) / 10, 2)))
        d += timedelta(days=1)
    allocations, _ = plan_range(days, WEIGHTS, seed=11)
    sums = day_sums(allocations)
    assert all(abs(sums[d] - h) < 0.005 for d, h in days)
    assert abs(sum(a.hours for a in allocations) - sum(h for _, h in days)) < 0.01


def test_mindestgewichtung():
    assert minimum_weight_pct(38) == 2.6
    assert minimum_weight_pct(0) == 0.0


def test_best_of_waehlt_bessere_verteilung():
    from distribution import score_reports
    _, single = plan_range(DAYS, WEIGHTS, seed=1)
    _, best, _ = plan_range_best_of(DAYS, WEIGHTS, seed=1, candidates=50)
    assert score_reports(best) <= score_reports(single)


def test_best_of_bleibt_tagesscharf():
    allocations, _, _ = plan_range_best_of(DAYS, WEIGHTS, seed=1, candidates=25)
    sums = day_sums(allocations)
    for d, hours in DAYS:
        assert abs(sums[d] - hours) < 0.005


def test_best_of_ist_reproduzierbar():
    a1, _, s1 = plan_range_best_of(DAYS, WEIGHTS, seed=5, candidates=30)
    a2, _, s2 = plan_range_best_of(DAYS, WEIGHTS, seed=5, candidates=30)
    assert a1 == a2 and s1 == s2
    # Der Gewinner-Seed allein reproduziert das Ergebnis ebenfalls.
    a3, _, _ = plan_range_best_of(DAYS, WEIGHTS, seed=s1, candidates=1)
    assert a3 == a1


# ── Projekte mit Wochen-Obergrenze ───────────────────────────────────────────

OPS2 = [("OPS-A", 0.60), ("OPS-B", 0.40)]
VOLLE_WOCHE = [
    (date(2026, 3, 2), 8.00), (date(2026, 3, 3), 6.56), (date(2026, 3, 4), 8.25),
    (date(2026, 3, 5), 7.50), (date(2026, 3, 6), 7.75),
]


def test_projekte_werden_zuerst_bedient():
    plan = Plan(ops=OPS2, projects=[("PRJ-1", 10.0), ("PRJ-2", 6.0)])
    targets, meta = compute_targets(38.0, 5, plan)
    assert targets["PRJ-1"] == 10.0
    assert targets["PRJ-2"] == 6.0
    # Der Rest teilt sich nach Gewicht auf: 22 h in 60/40
    assert abs(targets["OPS-A"] - 13.2) < 0.01
    assert abs(targets["OPS-B"] - 8.8) < 0.01
    assert not meta["projects_capped"]


def test_projektstunden_anteilig_in_randwochen():
    # Zwei von fuenf Arbeitstagen ergeben 40 Prozent der Obergrenze.
    plan = Plan(ops=OPS2, projects=[("PRJ-1", 10.0)])
    targets, _ = compute_targets(12.68, 2, plan)
    assert abs(targets["PRJ-1"] - 4.0) < 0.01
    assert abs(sum(targets.values()) - 12.68) < 0.01


def test_ueberzeichnung_kuerzt_anteilig_und_operations_geht_leer_aus():
    plan = Plan(ops=OPS2, projects=[("P1", 15.0), ("P2", 15.0), ("P3", 15.0)],
                priority="projects")
    targets, meta = compute_targets(38.0, 5, plan)
    assert meta["projects_capped"] and meta["ops_starved"]
    assert "OPS-A" not in targets
    # Gleiche Obergrenzen -> gleiche Anteile
    assert abs(targets["P1"] - targets["P3"]) < 0.01
    assert abs(sum(targets.values()) - 38.0) < 0.05


def test_vorrang_operations_sichert_den_mindestanteil():
    plan = Plan(ops=OPS2, projects=[("P1", 15.0), ("P2", 15.0), ("P3", 15.0)],
                priority="operations", ops_min_pct=30)
    targets, meta = compute_targets(38.0, 5, plan)
    ops_hours = targets["OPS-A"] + targets["OPS-B"]
    assert abs(ops_hours - 38.0 * 0.30) < 0.05
    assert meta["projects_capped"] and not meta["ops_starved"]


def test_mindestanteil_greift_nur_bei_knappheit():
    plan = Plan(ops=OPS2, projects=[("P1", 10.0)], priority="operations", ops_min_pct=30)
    targets, meta = compute_targets(38.0, 5, plan)
    assert targets["P1"] == 10.0            # nicht gekuerzt
    assert not meta["projects_capped"]


def test_projekte_ohne_operations_fuellen_die_woche():
    plan = Plan(ops=[], projects=[("P1", 30.0), ("P2", 30.0)])
    targets, meta = compute_targets(38.0, 5, plan)
    assert abs(sum(targets.values()) - 38.0) < 0.05
    assert not meta["ops_starved"]          # es gibt gar kein Operations


def test_tagessumme_bleibt_exakt_mit_projekten():
    plan = Plan(ops=OPS2, projects=[("PRJ-1", 10.0), ("PRJ-2", 6.0)])
    allocations, _, _ = plan_range_best_of(VOLLE_WOCHE, plan, seed=42, candidates=20)
    sums = day_sums(allocations)
    for d, hours in VOLLE_WOCHE:
        assert abs(sums[d] - hours) < 0.005


def test_projektstunden_werden_nicht_uebertragen():
    # Eine Woche unter der Obergrenze darf die naechste nicht aufblaehen.
    days = VOLLE_WOCHE + [(date(2026, 3, 9), 8.0), (date(2026, 3, 10), 8.0)]
    plan = Plan(ops=OPS2, projects=[("PRJ-1", 10.0)])
    _, reports = plan_range(days, plan, seed=7)
    zweite = reports[1]
    # Zweite Woche hat zwei Tage -> hoechstens 40 Prozent von 10 h
    assert zweite.project_hours <= 4.0 + 0.01


def test_bericht_weist_projekt_und_operationsstunden_aus():
    plan = Plan(ops=OPS2, projects=[("PRJ-1", 10.0)])
    _, reports = plan_range(VOLLE_WOCHE, plan, seed=3)
    r = reports[0]
    assert abs(r.project_hours + r.ops_hours - r.hours) < 0.01
    assert r.project_hours > 0 and r.ops_hours > 0


def test_leerer_plan_liefert_nichts():
    targets, _ = compute_targets(38.0, 5, Plan())
    assert targets == {}
