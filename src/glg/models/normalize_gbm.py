"""LightGBM weather-normalisation model (decision D4: the flexible second model).

Same contract and validation as the GAM (models/common.py): weather and calendar features
only, no time trend. Features: temperature, dewpoint, 24 h and 72 h mean temperature,
temperature 24 h earlier, local hour, day of week, day of year and day type (categorical).

Settings (fixed, chosen on ERCO/CISO/PJM/AZPS): 31 leaves, learning rate 0.05, 500 rounds,
at least 200 hours per leaf; 63 leaves / 800 rounds was no better. Deterministic, seed 42.

Caveat for Prompt 9: trees can't extrapolate. Above the hottest baseline temperature the
GBM predicts a flat load, so in record heat it under-predicts and inflates "growth". The
GAM extrapolates linearly instead.
"""

import lightgbm as lgb
import numpy as np
import polars as pl

from glg.models.common import day_type_code

PARAMS = {
    "objective": "l2",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "seed": 42,
    "deterministic": True,
    "num_threads": 4,
    "verbose": -1,
}
ROUNDS = 500


class GbmModel:
    name = "gbm"
    features = [
        "t2m_c",
        "d2m_c",
        "t2m_c_mean24",
        "t2m_c_mean72",
        "t2m_c_lag24",
        "local_hour",
        "dow",
        "doy",
        "day_type",
    ]

    def __init__(self, params: dict | None = None, rounds: int = ROUNDS):
        self.params = {**PARAMS, **(params or {})}
        self.rounds = rounds
        self.booster: lgb.Booster | None = None

    def _x(self, df: pl.DataFrame) -> np.ndarray:
        cols = [day_type_code(df) if c == "day_type" else df[c].to_numpy() for c in self.features]
        return np.column_stack(cols).astype(float)

    def fit(self, df: pl.DataFrame) -> "GbmModel":
        data = lgb.Dataset(
            self._x(df),
            df["demand_clean"].to_numpy(),
            feature_name=self.features,
            categorical_feature=["day_type"],
        )
        self.booster = lgb.train(self.params, data, self.rounds)
        return self

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        if self.booster is None:
            raise RuntimeError("fit first")
        return self.booster.predict(self._x(df))

    def contributions(self, df: pl.DataFrame) -> pl.DataFrame:
        """TreeSHAP contributions per feature (LightGBM's pred_contrib), plus the bias term."""
        if self.booster is None:
            raise RuntimeError("fit first")
        c = self.booster.predict(self._x(df), pred_contrib=True)
        return pl.DataFrame(c, schema=[*self.features, "expected_value"])
