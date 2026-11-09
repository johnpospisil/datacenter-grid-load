"""Each cleaning rule in glg.build.clean, on a small synthetic series."""

from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest

from glg.build import clean

N = 400


def series(values, imputed=None) -> pl.DataFrame:
    start = datetime(2019, 1, 1, 1, tzinfo=UTC)
    ts = pl.datetime_range(
        start, start + (len(values) - 1) * pl.duration(hours=1), "1h", eager=True, time_zone="UTC"
    )
    df = pl.DataFrame({"ba": "X", "period_end_utc": ts, "demand_raw": values})
    if imputed is not None:
        df = df.with_columns(demand_eia_imputed=pl.Series(imputed, dtype=pl.Float64))
    return df


def base() -> np.ndarray:
    """Daily cycle 800-1200 MW plus small noise: a plausible, flag-free load."""
    rng = np.random.default_rng(0)
    h = np.arange(N)
    return 1000 + 200 * np.sin(2 * np.pi * h / 24) + rng.normal(0, 5, N)


def flags(values, **kw) -> pl.DataFrame:
    return clean.add_flags(series(list(values), **kw), ["ba"])


def test_clean_series_has_no_flags():
    out = flags(base())
    assert not out.select(pl.any_horizontal(clean.FLAG_COLUMNS)).to_series().any()


def test_missing_negative_zero():
    v = base().tolist()
    v[50], v[100], v[120] = None, -5.0, 0.0
    out = flags(v)
    assert out["flag_missing"][50] and out["flag_missing"].sum() == 1
    assert out["flag_negative"][100] and out["flag_negative"].sum() == 1
    assert out["flag_zero"][120] and out["flag_zero"].sum() == 1


def test_flat_run_needs_six_identical_hours_and_keeps_the_first():
    v = base()
    v[150:156] = 1111.0  # 6 identical hours -> flag hours 2-6
    v[200:205] = 999.0  # 5 identical hours -> not flagged
    out = flags(v)["flag_flat_run"]
    assert out[151:156].all() and not out[150]
    assert not out[200:205].any()
    assert out.sum() == 5


def test_level_outlier_high_and_low():
    v = base()
    v[250] = 2.5 * 1000  # > 2x rolling median
    v[300] = 0.3 * 1000  # < 0.4x rolling median
    out = flags(v)["flag_level_outlier"]
    assert out[250] and out[300] and out.sum() == 2


def test_spike_flags_a_one_hour_excursion_but_not_a_step():
    v = base()
    v[180] += 400  # up and straight back down
    v[320:] += 400  # level shift: big jump in, no jump out
    out = flags(v)["flag_spike"]
    assert out[180]
    assert not out[320]


def test_eia_imputed_flag():
    imputed = [None] * N
    imputed[10] = 1000.0
    out = flags(base(), imputed=imputed)["flag_eia_imputed"]
    assert out[10] and out.sum() == 1


def test_clean_demand_interpolates_gaps_up_to_three_hours():
    v = base().tolist()
    for i in (60, 61, 62):  # 3-hour gap: filled
        v[i] = None
    for i in (90, 91, 92, 93):  # 4-hour gap: left null
        v[i] = None
    df = clean.add_clean_demand(clean.add_flags(series(v), ["ba"]), ["ba"])
    c = df["demand_clean"]
    assert c[60:63].is_not_null().all() and df["flag_interpolated"][60:63].all()
    assert c[61] == pytest.approx((v[59] + v[63]) / 2, rel=1e-9)
    assert c[90:94].is_null().all() and not df["flag_interpolated"][90:94].any()
    assert df["flag_interpolated"].sum() == 3


def test_clean_demand_masks_flagged_hours_and_leaves_raw_alone():
    v = base()
    v[150:160] = 1111.0  # 9 flagged hours: too long to interpolate
    df = clean.add_clean_demand(clean.add_flags(series(list(v)), ["ba"]), ["ba"])
    assert df["demand_raw"].to_list() == pytest.approx(list(v))
    assert df["demand_clean"][151:160].is_null().all()
    unflagged = df.filter(~pl.col("flag_any"))
    assert (unflagged["demand_clean"] == unflagged["demand_raw"]).all()


def test_complete_hours_inserts_missing_rows():
    df = series(base()[:10].tolist()).filter(~pl.int_range(pl.len()).is_in([3, 4]))
    out = clean.complete_hours(df, ["ba"])
    assert out.height == 10
    assert out["demand_raw"][3:5].is_null().all()
