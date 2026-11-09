"""Annual EIA-930 totals vs FERC Form 714 (data/reference/annual_benchmarks.csv).

The benchmark table is committed, so these tests run without the raw data. Every
BA-year that doesn't pass must be listed in KNOWN_FAILURES with a reason; a new
failure, or a known one that starts passing, fails the suite so it gets looked at.
Explanations are in DATA.md ("Annual reconciliation").
"""

import polars as pl
import pytest

from glg.build import benchmarks
from glg.config import INTERIM_DIR, REFERENCE_DIR

KNOWN_FAILURES = {
    ("AZPS", 2020): "FERC-714 2020 filing is ~6.8x too large all year (filing/units error)",
    ("AZPS", 2025): "EIA-930 above FERC from 2025-09 (8-22%/month); EIA balances (D = NG - TI)",
    ("DOPD", 2016): "EIA-930 ~7% below FERC until 2021, then equal: definition change",
    ("DOPD", 2017): "same DOPD definition change",
    ("DOPD", 2018): "same DOPD definition change",
    ("DOPD", 2019): "same DOPD definition change",
    ("DOPD", 2020): "too few overlapping hours (DOPD gaps)",
    ("FPL", 2016): "EIA-930 2.5-5% above FERC until 2023, then ~-1%: drift, unexplained",
    ("FPL", 2017): "same FPL drift",
    ("FPL", 2018): "same FPL drift",
    ("FPL", 2021): "same FPL drift",
    ("LGEE", 2020): "LGEE vs FERC moves -4% -> +19% over 2019-2024: definition/boundary change",
    ("LGEE", 2021): "same LGEE break",
    ("LGEE", 2022): "same LGEE break",
    ("LGEE", 2023): "same LGEE break",
    ("LGEE", 2024): "same LGEE break",
    ("LGEE", 2025): "same LGEE break",
    ("PACE", 2016): "too few overlapping hours (PACE/PACW 2016 gaps)",
    ("PACE", 2017): "PACE+PACW 3.4% below PacifiCorp filing",
    ("PACE", 2020): "PACE+PACW 4.2% below PacifiCorp filing",
    ("PSCO", 2024): "EIA-930 5-9% above FERC from 2023-08 on: unexplained break",
    ("PSCO", 2025): "same PSCO break",
    ("SWPP", 2018): "too few overlapping hours (SWPP stuck-value gaps)",
    ("SWPP", 2020): "FERC-714 low in Jan-Feb and Jun-Aug 2020 (EIA +17% to +42%)",
    (
        "SWPP",
        2025,
    ): "EIA-930 ~6-8% below FERC all of 2025: footprint change ahead of 2026-04 west expansion",
}

# Shortlisted series with no matching FERC-714 planning area.
NO_BENCHMARK = {"SOCO", "DOM"}


@pytest.fixture(scope="module")
def table():
    return pl.read_csv(REFERENCE_DIR / "annual_benchmarks.csv")


def test_every_shortlisted_series_has_a_benchmark_or_a_reason(table):
    shortlist = pl.read_csv(REFERENCE_DIR / "ba_shortlist.csv").filter(pl.col("include") != "no")
    missing = set(shortlist["series"]) - set(table["ba"]) - NO_BENCHMARK
    assert not missing


def test_benchmark_covers_every_year(table):
    years = table.group_by("ba").agg(pl.col("year").n_unique())
    assert (years["year"] == 10).all(), years.filter(pl.col("year") != 10)


def test_failures_are_exactly_the_known_ones(table):
    failing = {
        (r["ba"], r["year"]) for r in table.filter(pl.col("status") != "pass").iter_rows(named=True)
    }
    new = failing - KNOWN_FAILURES.keys()
    fixed = KNOWN_FAILURES.keys() - failing
    assert not new, f"New reconciliation failures: {sorted(new)}"
    assert not fixed, f"Known failures now pass (update KNOWN_FAILURES): {sorted(fixed)}"


def test_focus_bas_pass_in_the_baseline_years(table):
    """The weather baseline (2016-2019) must reconcile for every focus BA that has a benchmark."""
    focus = ["PJM", "ERCO", "AZPS", "SRP", "DUK", "TVA", "NEVP", "GCPD", "BPAT"]
    base = table.filter(pl.col("ba").is_in(focus), pl.col("year").is_between(2016, 2019))
    assert (base["status"] == "pass").all(), base.filter(pl.col("status") != "pass")


def _annual(ba, year, err, hours=8760, relation="same"):
    return {
        "ba": ba,
        "year": year,
        "hours_compared": hours,
        "eia930_twh": 1.0,
        "benchmark_twh": 1.0,
        "error_pct": err,
        "relation": relation,
    }


def test_reconcile_rules():
    annual = pl.DataFrame(
        [
            _annual("A", 2019, 2.9),
            _annual("A", 2021, -3.1),
            _annual("A", 2022, 0.0, hours=7000),
            # definition difference: steady +30% passes, a 4 pp jump off the median fails
            _annual("B", 2019, 30.0, relation="definition_difference"),
            _annual("B", 2021, 31.0, relation="definition_difference"),
            _annual("B", 2022, 35.0, relation="definition_difference"),
        ]
    )
    mapping = pl.DataFrame({"ba": ["A", "B"], "relation": ["same", "definition_difference"]})
    out = benchmarks.reconcile(annual.drop("relation"), mapping)
    assert out["status"].to_list() == [
        "pass",
        "fail",
        "insufficient_overlap",
        "pass",
        "pass",
        "fail",
    ]


PJM_DOM = INTERIM_DIR / "pjm_dom_hourly.parquet"


@pytest.mark.skipif(not PJM_DOM.exists(), reason="PJM DOM export not present (internal check)")
def test_dom_matches_pjm_metered_load():
    """Internal only (PJM licence): EIA-930 DOM within 3% of PJM metered load, 2019 on."""
    from glg.build.clean import SUBREGION_CLEAN_PARQUET

    e = pl.read_parquet(
        SUBREGION_CLEAN_PARQUET, columns=["subregion", "period_end_utc", "demand_clean"]
    )
    e = e.filter(pl.col("subregion") == "DOM").drop("subregion")
    j = pl.read_parquet(PJM_DOM).join(e, on="period_end_utc").drop_nulls("demand_clean")
    yearly = j.group_by(pl.col("period_end_utc").dt.year().alias("y")).agg(
        err=(pl.col("demand_clean").sum() / pl.col("mw").sum() - 1) * 100
    )
    yearly = yearly.filter(pl.col("y") >= 2019)
    assert (yearly["err"].abs() <= 3).all(), yearly
