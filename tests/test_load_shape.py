"""Load-shape decomposition: planted flat, weather and daytime growth come back out."""

from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest

from glg.models import growth as g
from glg.models import load_shape as ls


def synthetic(extra) -> ls.Shape:
    """Two years of hours (2022 and 2023); `extra(hour, cdh)` is added to actual in 2023."""
    t = pl.datetime_range(
        datetime(2022, 1, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, 0, tzinfo=UTC),
        "1h",
        eager=True,
        time_unit="us",
    )
    rng = np.random.default_rng(0)
    hour = t.dt.offset_by("-1h").dt.hour().to_numpy()  # treat UTC as local
    doy = t.dt.ordinal_day().to_numpy()
    temp_f = 60 + 20 * np.sin(2 * np.pi * (doy - 110) / 365) + 8 * np.sin(np.pi * hour / 24)
    cdh, hdh = np.clip(temp_f - 65, 0, None), np.clip(65 - temp_f, 0, None)
    pred = 1000 + 300 * np.sin(np.pi * np.clip(hour - 6, 0, 14) / 14) + 20 * cdh + 10 * hdh
    year = t.dt.offset_by("-1h").dt.year().to_numpy()
    actual = pred + rng.normal(0, 5, len(t)) + np.where(year == 2023, extra(hour, cdh), 0.0)
    df = pl.DataFrame(
        {
            "period_end_utc": t,
            "actual": actual,
            "pred_gam": pred,
            "pred_gbm": pred,
            "local_hour": hour,
            "cdh65f": cdh,
            "hdh65f": hdh,
            "in_baseline": year == 2022,
            "is_covid": np.zeros(len(t), bool),
        }
    )
    return ls.Shape("SYN", ls._with_time(df))


RULE = g.Rule(start_year=2022, last_month=datetime(2023, 12, 1).date())


def change(extra):
    return ls.change_since(synthetic(extra), RULE, np.random.default_rng(1))


def test_flat_load_is_all_floor():
    r = change(lambda h, c: np.full(len(h), 500.0))
    assert r["d_total_mw"] == pytest.approx(500, abs=2)
    assert r["d_flat_mw"] == pytest.approx(500, abs=3)
    assert abs(r["d_weather_mw"]) < 3 and abs(r["d_shape_mw"]) < 3
    assert r["d_night_mw"] == pytest.approx(r["d_afternoon_mw"], abs=3)
    assert r["verdict"] == "24/7 floor"
    assert r["d_flat_mw_lo"] <= 500 <= r["d_flat_mw_hi"] + 1


def test_cooling_growth_is_weather_sensitive():
    r = change(lambda h, c: 15.0 * c)
    assert r["d_weather_mw"] == pytest.approx(r["d_total_mw"], rel=0.02)
    assert abs(r["d_flat_mw"]) < 5


def test_daytime_only_growth_fails_the_floor_test():
    r = change(lambda h, c: np.where((h >= 10) & (h <= 18), 900.0, 0.0))
    assert abs(r["d_flat_mw"]) < 5
    assert r["d_shape_mw"] == pytest.approx(r["d_total_mw"], rel=0.02)
    assert r["d_night_mw"] == pytest.approx(0, abs=3)
    assert r["verdict"] == "daytime/peak"


def test_components_add_up():
    r = change(lambda h, c: 300 + 5.0 * c + np.where(h == 15, 200.0, 0.0))
    parts = r["d_flat_mw"] + r["d_weather_mw"] + r["d_shape_mw"]
    assert parts == pytest.approx(r["d_total_mw"], abs=1e-6)


@pytest.mark.parametrize(
    "night, after, lo, verdict",
    [
        (100, 100, 50, "24/7 floor"),
        (85, 100, 50, "24/7 floor"),
        (70, 100, 50, "mixed"),
        (20, 100, 50, "daytime/peak"),
        (50, -10, 5, "24/7 floor"),  # afternoon fell (e.g. rooftop solar), night rose
        (100, 100, -5, "no growth"),
    ],
)
def test_verdict_rules(night, after, lo, verdict):
    r = {"d_total_mw": 100, "d_total_mw_lo": lo, "d_total_mw_hi": 200}
    assert ls.verdict(r | {"d_night_mw": night, "d_afternoon_mw": after}) == verdict


def test_every_pair_names_a_reason():
    assert all(len(reason) > 20 for _, reason in ls.PAIRS.values())


# --------------------------------------------------------------------------- built outputs

needs_outputs = pytest.mark.skipif(not ls.DECOMP_PATH.exists(), reason="run load_shape")

DC = ["DOM", "ERCO", "AZPS+SRP", "NEVP", "GCPD", "BPAT"]
COMP = ["MISO", "NYIS", "ISNE", "FPC", "CPLE", "CISO", "PACE", "FPL"]


@pytest.fixture(scope="module")
def dec():
    return {r["ba"]: r for r in pl.read_csv(ls.DECOMP_PATH).iter_rows(named=True)}


@needs_outputs
def test_decomposition_covers_every_series(dec):
    ranking = pl.read_csv(g.RANKING_PATH)
    assert set(dec) == set(ranking["ba"])


@needs_outputs
def test_data_center_growth_sits_in_the_floor(dec):
    """The core claim's second half (brief: 'wrong if ... only at daytime/peak hours')."""
    assert all(dec[b]["verdict"] == "24/7 floor" for b in DC)
    dc_night = np.median([dec[b]["night_growth_pct"] for b in DC])
    comp_night = np.median([dec[b]["night_growth_pct"] for b in COMP])
    assert dc_night > 3 * comp_night


@needs_outputs
def test_pinned_dominion_shape(dec):
    """Pinned (Prompt 10): DOM adds ~3.2 GW at night and in the afternoon alike."""
    d = dec["DOM"]
    assert d["d_night_mw"] == pytest.approx(3236, abs=150)
    assert d["night_to_afternoon"] == pytest.approx(1.00, abs=0.05)
    assert d["flat_share"] == pytest.approx(1.03, abs=0.1)
    assert abs(d["flat_share_gbm"] - d["flat_share"]) < 0.1
