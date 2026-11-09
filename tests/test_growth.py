"""Structural growth aggregation: planted steps and trends come back out exactly."""

from datetime import UTC, date, datetime

import numpy as np
import polars as pl
import pytest

from glg.models import growth as g


def test_jackknife_se_by_hand():
    # mean 2.5, squared deviations sum to 5; sqrt(3/4 * 5)
    assert g.jackknife_se(np.array([1.0, 2.0, 3.0, 4.0])) == pytest.approx(np.sqrt(3.75))


def test_block_bootstrap_of_a_constant_ratio_is_that_ratio():
    rng = np.random.default_rng(0)
    p = rng.uniform(900, 1100, 365)
    r = g.block_bootstrap(1.03 * p, p, rng)
    np.testing.assert_allclose(r, 1.03)


def test_months_back_crosses_the_year():
    assert g._months_back(date(2026, 2, 1), 3) == [
        date(2026, 2, 1),
        date(2026, 1, 1),
        date(2025, 12, 1),
    ]


def synthetic_series(name="SYN", step=0.05, step_from=datetime(2023, 1, 1), trend=0.0) -> g.Series:
    """Counterfactual with a daily cycle; actual = counterfactual x (1 + trend) x (1 + step)."""
    t = pl.datetime_range(
        datetime(2016, 1, 1, 1, tzinfo=UTC),
        datetime(2026, 10, 31, 23, tzinfo=UTC),
        "1h",
        eager=True,
        time_unit="us",
    )
    hour = t.dt.hour().to_numpy()
    pred = 1000 + 200 * np.sin(2 * np.pi * hour / 24)
    years = ((t - datetime(2018, 1, 1, tzinfo=UTC)).dt.total_hours() / 8766).to_numpy()
    after = (t > step_from.replace(tzinfo=UTC)).to_numpy()  # by the hour's start
    actual = pred * (1 + trend / 100 * years) * np.where(after, 1 + step, 1.0)
    yr = t.dt.year().to_numpy()
    hourly = pl.DataFrame(
        {
            "period_end_utc": t,
            "actual": actual,
            "pred_gam": pred,
            "pred_gbm": pred,
            "pred_ddr": pred,
            "in_baseline": (yr >= 2016) & (yr <= 2019),
            "is_covid": (yr == 2020) | (yr == 2021),
        }
    )
    return g.Series(name, hourly, np.column_stack([pred] * 4))


@pytest.fixture(scope="module")
def step_result():
    s = synthetic_series()
    rng = np.random.default_rng(1)
    m = g.aggregate(s, "month", rng)
    return s, m, g.growth_since(s, m, g.Rule(), rng)


def test_planted_step_is_recovered(step_result):
    _, _, r = step_result
    assert r["growth_pct"] == pytest.approx(5.0, abs=1e-6)
    assert r["growth_lo"] - 1e-9 <= 5.0 <= r["growth_hi"] + 1e-9
    assert r["baseline_trend_pct_yr"] == pytest.approx(0.0, abs=1e-6)
    assert r["beyond_trend_pct"] == pytest.approx(5.0, abs=1e-6)
    assert r["mw_added"] == pytest.approx(50.0, rel=1e-3)  # 5% of a 1,000 MW mean


def test_annual_index_is_100_in_2019_and_105_after_the_step():
    s = synthetic_series()
    y = g.aggregate(s, "year", np.random.default_rng(2)).sort("year")
    idx = dict(zip(y["year"], y["index_2019"], strict=True))
    assert idx[2019] == pytest.approx(100.0)
    assert idx[2024] == pytest.approx(105.0)
    assert y["year"].min() == 2016  # no stray 2015 row


def test_rolling_index_reaches_105(step_result):
    s, m, _ = step_result
    roll = g.rolling_index(s, m).sort("month")
    assert roll.filter(pl.col("month") == date(2019, 12, 1))["index_12m"][0] == pytest.approx(100)
    assert roll["index_12m"][-1] == pytest.approx(105)


def test_baseline_trend_is_extended_forward_d11():
    """A BA growing 2%/yr all along shows growth since 2022 but none beyond its own trend."""
    s = synthetic_series(step=0.0, trend=2.0)
    rng = np.random.default_rng(3)
    m = g.aggregate(s, "month", rng)
    r = g.growth_since(s, m, g.Rule(), rng)
    assert r["baseline_trend_pct_yr"] == pytest.approx(2.0, abs=0.1)
    assert r["growth_pct"] > 6  # ~4 years of 2%/yr
    assert r["beyond_trend_pct"] == pytest.approx(0.0, abs=0.3)


def test_combined_series_sums_hours():
    a, b = synthetic_series("A", step=0.10), synthetic_series("B", step=0.0)
    parts = [(x.hourly, x.jk, []) for x in (a, b)]
    c = g.combine("A+B", parts)
    np.testing.assert_allclose(c.hourly["actual"], a.hourly["actual"] + b.hourly["actual"])
    rng = np.random.default_rng(4)
    r = g.growth_since(c, g.aggregate(c, "month", rng), g.Rule(), rng)
    assert r["growth_pct"] == pytest.approx(5.0, abs=1e-6)  # half the load grew 10%


def test_rules_cover_the_known_breaks():
    assert g.RULES["FPL"].start_year == g.RULES["SOCO"].start_year == 2023
    assert g.RULES["AZPS"].last_month == date(2025, 8, 1)
    assert g.COMBINED["FPL+SOCO"] == ["FPL", "SOCO"]


# --------------------------------------------------------------------------- built outputs

needs_outputs = pytest.mark.skipif(not g.RANKING_PATH.exists(), reason="run glg.models.growth")


@needs_outputs
def test_ranking_outputs():
    from glg.build.features import modelled_bas

    r = pl.read_csv(g.RANKING_PATH)
    assert set(modelled_bas()) | {*g.COMBINED, *g.ALIASES} == set(r["ba"])
    assert not r.filter(pl.col("ba").is_in(list(g.NOT_HEADLINE)))["headline"].any()
    inside = (r["growth_lo"] <= r["growth_pct"]) & (r["growth_pct"] <= r["growth_hi"])
    assert inside.all()
    a = pl.read_csv(g.ANNUAL_PATH)
    assert a["year"].min() == g.FIRST_YEAR
    assert a.filter(pl.col("year") == g.INDEX_YEAR)["index_2019"].to_list() == pytest.approx(
        [100.0] * a.filter(pl.col("year") == g.INDEX_YEAR).height
    )
