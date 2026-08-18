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
from distribution import plan_range, minimum_weight_pct  # noqa: E402

WEIGHTS = [
    ("DEO1111-NP/PJ00-O51.0000", 0.40),
    ("DEO2222-NP/PJ00-O51.0000", 0.25),
    ("DEO3333-NP/PJ00-O51.0000", 0.15),
    ("DEO4444-NP/PJ00-O51.0000", 0.15),
    ("DEO5555000/PQ00-A02.0000", 0.05),
]

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
    for wbs, weight in WEIGHTS:
        actual = per_wbs.get(wbs, 0.0) / total * 100
        assert abs(actual - weight * 100) < 6.0, f"{wbs}: {actual:.1f}% statt {weight*100}%"


def test_einzelner_tag_funktioniert():
    allocations, _ = plan_range([(date(2026, 3, 16), 7.55)], WEIGHTS, seed=3)
    assert abs(sum(a.hours for a in allocations) - 7.55) < 0.005


def test_leerer_zeitraum():
    allocations, reports = plan_range([], WEIGHTS, seed=1)
    assert allocations == [] and reports == []


def test_ein_einziges_wbs_element_bekommt_alles():
    allocations, _ = plan_range(DAYS, [("NUR-EINS", 1.0)], seed=5)
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
    from distribution import plan_range_best_of, score_reports
    _, single = plan_range(DAYS, WEIGHTS, seed=1)
    _, best, _ = plan_range_best_of(DAYS, WEIGHTS, seed=1, candidates=50)
    assert score_reports(best) <= score_reports(single)


def test_best_of_bleibt_tagesscharf():
    from distribution import plan_range_best_of
    allocations, _, _ = plan_range_best_of(DAYS, WEIGHTS, seed=1, candidates=25)
    sums = day_sums(allocations)
    for d, hours in DAYS:
        assert abs(sums[d] - hours) < 0.005


def test_best_of_ist_reproduzierbar():
    from distribution import plan_range_best_of
    a1, _, s1 = plan_range_best_of(DAYS, WEIGHTS, seed=5, candidates=30)
    a2, _, s2 = plan_range_best_of(DAYS, WEIGHTS, seed=5, candidates=30)
    assert a1 == a2 and s1 == s2
    # Der Gewinner-Seed allein reproduziert das Ergebnis ebenfalls.
    a3, _, _ = plan_range_best_of(DAYS, WEIGHTS, seed=s1, candidates=1)
    assert a3 == a1
