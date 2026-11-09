"""Baseline weather-normalisation models: metrics, folds, no-trend guard, synthetic recovery."""

from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest

from glg.models import validate as v
from glg.models.common import ALLOWED_FEATURES, fold_ids, metrics
from glg.models.normalize_ddr import DdrModel
from glg.models.normalize_gam import GamModel
from glg.models.normalize_gbm import GbmModel

MODELS = [GamModel, GbmModel, DdrModel]

# --------------------------------------------------------------------------- metrics, folds


def test_metrics_by_hand():
    a = np.array([100.0, 200.0])
    p = np.array([110.0, 210.0])  # +10 MW every hour: pure bias, perfect shape
    m = metrics(a, p)
    assert m["mape_pct"] == pytest.approx(100 * (0.1 + 0.05) / 2)
    assert m["shape_mape_pct"] == pytest.approx(0.0)
    assert m["bias_pct"] == pytest.approx(100 * 10 / 150)
    assert m["rmse_mw"] == pytest.approx(10.0)


def test_folds_are_years_or_months_for_short_baselines():
    t = [datetime(2018, 12, 31, 23, tzinfo=UTC), datetime(2019, 1, 1, 1, tzinfo=UTC)]
    df = pl.DataFrame({"period_end_utc": t})
    assert fold_ids(df, "ERCO").tolist() == [2018, 2019]
    assert fold_ids(df, "DOM").tolist() == [201812, 201901]


@pytest.mark.parametrize("cls", MODELS)
def test_models_use_no_trend_or_economic_features(cls):
    assert set(cls.features) <= ALLOWED_FEATURES
    assert not {"trend_years", "emp_index", "period_end_utc", "is_covid"} & set(cls.features)


# --------------------------------------------------------------------------- synthetic data


def synthetic(years=(2016, 2017, 2018, 2019), seed=0) -> pl.DataFrame:
    """Hourly demand that depends only on weather and calendar, plus 1% noise."""
    rng = np.random.default_rng(seed)
    start = datetime(years[0], 1, 1, tzinfo=UTC)
    end = datetime(years[-1], 12, 31, 23, tzinfo=UTC)
    t = pl.datetime_range(start, end, "1h", eager=True, time_unit="us")
    n = t.len()
    hour = t.dt.hour().to_numpy()
    doy = t.dt.ordinal_day().to_numpy()
    season = -np.cos(2 * np.pi * (doy - 15) / 365.25)
    temp = 13 + 13 * season + 5 * np.sin(2 * np.pi * (hour - 9) / 24) + rng.normal(0, 3, n)
    mean72 = pl.Series(temp).rolling_mean(72, min_samples=1).to_numpy()
    mean24 = pl.Series(temp).rolling_mean(24, min_samples=1).to_numpy()
    f = temp * 9 / 5 + 32
    dow = t.dt.weekday().to_numpy()
    day_type = np.where(dow == 6, "saturday", np.where(dow == 7, "sunday", "weekday"))
    daily = 1 + 0.25 * np.sin(np.pi * np.clip(hour - 6, 0, 16) / 16)
    y = (
        1000 * daily * np.where(day_type == "weekday", 1.0, 0.9)
        + 40 * np.clip(temp - 20, 0, None) * daily
        + 25 * np.clip(12 - mean72, 0, None)
    )
    y = y * (1 + rng.normal(0, 0.01, n))
    return pl.DataFrame(
        {
            "ba": "SYN",
            "period_end_utc": t,
            "t2m_c": temp,
            "d2m_c": temp - 5,
            "t2m_c_mean72": mean72,
            "t2m_c_mean24": mean24,
            "t2m_c_lag24": np.concatenate([temp[:24], temp[:-24]]),
            "dow": dow - 1,
            "local_hour": hour,
            "doy": doy,
            "month": t.dt.month().to_numpy(),
            "day_type": day_type,
            "cdh65f": np.clip(f - 65, 0, None),
            "hdh65f": np.clip(65 - f, 0, None),
            "cdh75f": np.clip(f - 75, 0, None),
            "hdh55f": np.clip(55 - f, 0, None),
            "demand_clean": y,
        }
    )


@pytest.fixture(scope="module")
def syn():
    return synthetic()


@pytest.mark.parametrize("cls, max_mape", [(GamModel, 2.5), (GbmModel, 2.5), (DdrModel, 4.0)])
def test_models_recover_a_weather_only_series(syn, cls, max_mape):
    train = syn.filter(pl.col("period_end_utc").dt.year() < 2019)
    test = syn.filter(pl.col("period_end_utc").dt.year() == 2019)
    m = metrics(test["demand_clean"].to_numpy(), cls().fit(train).predict(test))
    assert m["mape_pct"] < max_mape
    assert abs(m["bias_pct"]) < 1.0


@pytest.mark.parametrize("cls", MODELS)
def test_same_weather_and_calendar_give_same_prediction_in_any_year(syn, cls):
    """With no trend term, moving the same hours to another year can't change the forecast."""
    m = cls().fit(syn.filter(pl.col("period_end_utc").dt.year() < 2019))
    test = syn.filter(pl.col("period_end_utc").dt.year() == 2019)
    later = test.with_columns(pl.col("period_end_utc").dt.offset_by("6y"))
    np.testing.assert_allclose(m.predict(test), m.predict(later))


def test_residual_trend_finds_a_planted_trend(syn):
    """1.5 %/yr growth that the model can't see shows up as the residual trend."""
    years = syn["period_end_utc"].dt.year().to_numpy()
    grown = syn.with_columns(pl.col("demand_clean") * (1 + 0.015 * (years - 2017.5)))
    p = v.cross_validate(grown, "SYN", GamModel)
    tr = v.residual_trend(p)
    assert tr["trend_pct_per_yr"] == pytest.approx(1.5, abs=0.3)
    # ... and the held-out years carry it as bias: 2016 over-predicted, 2019 under.
    first = p.filter(pl.col("period_end_utc").dt.year() == 2016)
    assert (first["pred_oof"] - first["actual"]).mean() > 0


def test_gbm_contributions_add_up_to_the_prediction(syn):
    m = GbmModel().fit(syn.filter(pl.col("period_end_utc").dt.year() < 2019))
    test = syn.filter(pl.col("period_end_utc").dt.year() == 2019).head(500)
    c = m.contributions(test)
    np.testing.assert_allclose(c.sum_horizontal().to_numpy(), m.predict(test), rtol=1e-6)


def test_gbm_is_flat_above_the_training_range_and_gam_is_not(syn):
    """The extrapolation caveat, made concrete: +5 C beyond the hottest training hour."""
    train = syn.filter(pl.col("period_end_utc").dt.year() < 2019)
    hot = train.filter(pl.col("t2m_c") == pl.col("t2m_c").max())
    hotter = hot.with_columns(pl.col(c) + 5 for c in ["t2m_c", "d2m_c", "t2m_c_mean24"])
    gbm, gam = GbmModel().fit(train), GamModel().fit(train)
    assert gbm.predict(hotter)[0] == pytest.approx(gbm.predict(hot)[0], rel=0.01)
    assert gam.predict(hotter)[0] > gam.predict(hot)[0] * 1.05


def test_temperature_range_share():
    t = pl.DataFrame(
        {
            "ba": ["A"] * 4,
            "period_end_utc": [datetime(y, 7, 1, tzinfo=UTC) for y in (2018, 2019, 2023, 2024)],
            "in_baseline": [True, True, False, False],
            "demand_clean": [1.0] * 4,
            "t2m_c": [30.0, 35.0, 36.0, 20.0],
        }
    )
    r = v.temperature_range(t).row(0, named=True)
    assert r["pct_hours_2022on_above_base_max"] == 50.0
    assert r["pct_hours_2022on_below_base_min"] == 50.0


# --------------------------------------------------------------------------- built outputs

needs_outputs = pytest.mark.skipif(
    not v.FLAGS_PATH.exists(), reason="run python -m glg.models.validate"
)


@needs_outputs
def test_validation_outputs_cover_every_ba_and_model():
    from glg.build.features import modelled_bas

    flags = pl.read_csv(v.FLAGS_PATH)
    assert flags.height == len(modelled_bas()) * len(v.MODELS)
    assert set(flags["status"]) <= {"pass", "flag", "fail"}
    m = pl.read_csv(v.METRICS_PATH)
    assert m.filter(pl.col("holdout") == "all").height == flags.height
    comp = pl.read_csv(v.COMPARISON_PATH)
    assert comp.height == len(modelled_bas())
