"""Shared plumbing for the weather-normalisation models (Prompts 7-9).

Every model is fitted per BA on the baseline (2016-2019, COVID excluded) and must not see
any time trend: the point is to ask "what would demand be today with the 2016-2019
relationship to weather and calendar?". The gap between actual and that counterfactual is
the structural growth (Prompt 9).

A model is any object with `fit(df) -> self` and `predict(df) -> np.ndarray`, where `df` is
a slice of the modelling table. Models may use only the columns in `ALLOWED_FEATURES`.
"""

from typing import Protocol

import numpy as np
import polars as pl

from glg.build import features as feat

TARGET = "demand_clean"

# Weather and calendar only. trend_years, emp_index and anything derived from the date's
# position in time are deliberately absent (tested).
ALLOWED_FEATURES = {
    "t2m_c",
    "d2m_c",
    "rh_pct",
    "hi_c",
    "cdh55f",
    "hdh55f",
    "cdh65f",
    "hdh65f",
    "cdh75f",
    "hdh75f",
    "t2m_c_mean24",
    "t2m_c_mean72",
    "t2m_c_lag24",
    "local_hour",
    "dow",
    "month",
    "doy",
    "day_type",
    "is_weekend",
    "is_holiday",
    "is_bridge",
    "is_dst",
}

DAY_TYPES = ["weekday", "saturday", "sunday", "holiday_minor", "holiday_major", "bridge"]

# BAs whose EIA-930 baseline is too short for leave-one-year-out (DOM: 2018-09 -> 2019-12).
# They are validated by leaving out one calendar month at a time instead.
SHORT_BASELINE = {"DOM"}


class Model(Protocol):
    name: str
    features: list[str]

    def fit(self, df: pl.DataFrame) -> "Model": ...

    def predict(self, df: pl.DataFrame) -> np.ndarray: ...


def day_type_code(df: pl.DataFrame) -> np.ndarray:
    return df["day_type"].replace_strict(DAY_TYPES, list(range(len(DAY_TYPES)))).to_numpy()


def load_table(bas: list[str] | None = None) -> pl.DataFrame:
    t = pl.read_parquet(feat.OUT_PATH)
    return t.filter(pl.col("ba").is_in(bas)) if bas else t


def baseline_rows(table: pl.DataFrame, features: list[str]) -> pl.DataFrame:
    """Baseline hours with a clean target and every feature present."""
    need = [TARGET, *features]
    return table.filter(pl.col("in_baseline")).drop_nulls(need)


def fold_ids(df: pl.DataFrame, ba: str) -> np.ndarray:
    """Validation fold per row: the calendar year (UTC), or year-month for short baselines."""
    t = df["period_end_utc"]
    if ba in SHORT_BASELINE:
        return (t.dt.year() * 100 + t.dt.month()).to_numpy()
    return t.dt.year().to_numpy()


def metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    """MAPE, R2, bias and 'shape' MAPE (after removing the mean error), all in %."""
    e = pred - actual
    shape = e - e.mean()
    ss_tot = np.sum((actual - actual.mean()) ** 2)
    return {
        "n_hours": int(actual.size),
        "mape_pct": float(100 * np.mean(np.abs(e) / actual)),
        "shape_mape_pct": float(100 * np.mean(np.abs(shape) / actual)),
        "bias_pct": float(100 * e.mean() / actual.mean()),
        "r2": float(1 - np.sum(e**2) / ss_tot) if ss_tot > 0 else float("nan"),
        "rmse_mw": float(np.sqrt(np.mean(e**2))),
    }
