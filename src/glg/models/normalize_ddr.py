"""Degree-day regression: the simple OLS benchmark and sanity floor (decision D4).

    demand ~ hour x day-type fixed effects (24 x 6) + month fixed effects
           + CDH65 x hour + HDH65 x hour            hour-specific heating/cooling slopes
           + CDH75 + HDH55                          extra slope in the extremes
           + CDD and HDD of the 24 h and 72 h mean temperature (base 65 F)

Degree-hours are population-weighted county values (Prompt 5), in degree F. Plain least
squares in MW; no trend term.
"""

import numpy as np
import polars as pl

from glg.models.common import DAY_TYPES

BASE_F = 65.0


def _deg(c: pl.Expr) -> tuple[pl.Expr, pl.Expr]:
    f = c * 9 / 5 + 32
    return (f - BASE_F).clip(lower_bound=0), (BASE_F - f).clip(lower_bound=0)


class DdrModel:
    name = "ddr"
    features = [
        "local_hour",
        "day_type",
        "month",
        "cdh65f",
        "hdh65f",
        "cdh75f",
        "hdh55f",
        "t2m_c_mean24",
        "t2m_c_mean72",
    ]

    def __init__(self):
        self.coef: np.ndarray | None = None

    @staticmethod
    def _x(df: pl.DataFrame) -> np.ndarray:
        cdd24, hdd24 = _deg(pl.col("t2m_c_mean24"))
        cdd72, hdd72 = _deg(pl.col("t2m_c_mean72"))
        hour = pl.col("local_hour")
        exprs = [
            ((hour == h) & (pl.col("day_type") == d)).cast(pl.Float64)
            for d in DAY_TYPES
            for h in range(24)
        ]
        exprs += [(pl.col("month") == m).cast(pl.Float64) for m in range(2, 13)]
        exprs += [pl.col("cdh65f") * (hour == h) for h in range(24)]
        exprs += [pl.col("hdh65f") * (hour == h) for h in range(24)]
        exprs += [pl.col("cdh75f"), pl.col("hdh55f"), cdd24, hdd24, cdd72, hdd72]
        return df.select(
            [e.cast(pl.Float64).alias(f"x{i}") for i, e in enumerate(exprs)]
        ).to_numpy()

    def fit(self, df: pl.DataFrame) -> "DdrModel":
        self.coef, *_ = np.linalg.lstsq(self._x(df), df["demand_clean"].to_numpy(), rcond=None)
        return self

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        if self.coef is None:
            raise RuntimeError("fit first")
        return self._x(df) @ self.coef
