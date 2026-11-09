"""GAM weather-normalisation model (decision D4: the primary, interpretable model).

    demand ~ te(temperature, local hour)       how the temperature response varies by hour
           + s(72 h mean temperature)          thermal inertia of buildings
           + s(dewpoint)                       humidity (latent cooling load)
           + te(day of year, local hour)       season and daylight (incl. rooftop solar)
           + te(local hour, day type)          daily shape for weekdays, weekends, holidays

Fitted in MW with pygam's LinearGAM and fixed smoothing (lam = 0.6, chosen on 4 BAs:
heavier smoothing was slightly worse, a log target no better). Hour and day of year use
cyclic bases that wrap 24 -> 0 and 366 -> 1. Outside the baseline's temperature range the
splines extrapolate linearly. No time trend, by design (see models/common.py).
"""

import numpy as np
import polars as pl
from pygam import LinearGAM, s, te

from glg.models.common import day_type_code

LAM = 0.6
HOUR_KNOTS = np.array([0.0, 24.0])
DOY_KNOTS = np.array([1.0, 367.0])


class GamModel:
    name = "gam"
    features = ["t2m_c", "local_hour", "t2m_c_mean72", "d2m_c", "doy", "day_type"]

    def __init__(self, lam: float = LAM):
        self.lam = lam
        self.gam: LinearGAM | None = None

    @staticmethod
    def _x(df: pl.DataFrame) -> np.ndarray:
        cols = [df[c].to_numpy() for c in ["t2m_c", "local_hour", "t2m_c_mean72", "d2m_c", "doy"]]
        return np.column_stack([*cols, day_type_code(df)]).astype(float)

    @staticmethod
    def _terms():
        return (
            te(0, 1, n_splines=[10, 12], basis=["ps", "cp"], edge_knots=[None, HOUR_KNOTS])
            + s(2, n_splines=10)
            + s(3, n_splines=10)
            + te(4, 1, n_splines=[10, 8], basis=["cp", "cp"], edge_knots=[DOY_KNOTS, HOUR_KNOTS])
            + te(
                1,
                5,
                n_splines=[12, 6],
                basis=["cp", "ps"],
                dtype=["numerical", "categorical"],
                edge_knots=[HOUR_KNOTS, None],
            )
        )

    def fit(self, df: pl.DataFrame) -> "GamModel":
        self.gam = LinearGAM(self._terms(), lam=self.lam).fit(self._x(df), df["demand_clean"])
        return self

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        if self.gam is None:
            raise RuntimeError("fit first")
        return self.gam.predict(self._x(df))
