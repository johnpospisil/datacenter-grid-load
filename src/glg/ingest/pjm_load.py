"""PJM Data Miner 2 `hrl_load_metered` for Dominion (DOM), from manual CSV exports.

The Data Miner API needs a key we could not get, so the yearly CSVs were exported
by hand from dataminer2.pjm.com (Hourly Load: Metered, Load Area = DOM) into
`data/raw/pjm/hrl_load_metered_<year>.csv`. This module only reads them.

PJM timestamps are hour-*beginning*; we convert to hour-ending UTC to match EIA-930.

Licence: Data Miner terms say the data is for internal use only and that
redistributing it, or data derived from it, needs a PJM Redistribution License.
So this series is used as an internal cross-check and baseline-sensitivity input
only. Never commit the raw files or publish figures/numbers derived from them.

Run: python -m glg.ingest.pjm_load
"""

import logging
from pathlib import Path

import polars as pl

from glg.config import INTERIM_DIR, RAW_DIR

log = logging.getLogger(__name__)

RAW_PJM_DIR = RAW_DIR / "pjm"
OUT_PATH = INTERIM_DIR / "pjm_dom_hourly.parquet"
TS_FORMAT = "%m/%d/%Y %I:%M:%S %p"


def read_export(path: Path) -> pl.DataFrame:
    """Read one Data Miner CSV export into (zone, load_area, period_end_utc, mw, is_verified)."""
    df = pl.read_csv(path, infer_schema_length=0)
    return df.select(
        pl.col("zone"),
        pl.col("load_area"),
        (
            pl.col("datetime_beginning_utc")
            .str.strptime(pl.Datetime("us"), TS_FORMAT)
            .dt.replace_time_zone("UTC")
            + pl.duration(hours=1)
        ).alias("period_end_utc"),
        pl.col("mw").cast(pl.Float64),
        (pl.col("is_verified").str.to_lowercase() == "true").alias("is_verified"),
        pl.lit(path.name).alias("source_file"),
    )


def build(raw_dir: Path = RAW_PJM_DIR, load_area: str = "DOM") -> pl.DataFrame:
    files = sorted(raw_dir.glob("hrl_load_metered_*.csv"))
    if not files:
        raise FileNotFoundError(f"No hrl_load_metered_*.csv exports in {raw_dir}")
    df = pl.concat([read_export(f) for f in files])
    other = df.filter(pl.col("load_area") != load_area)["load_area"].unique().to_list()
    if other:
        raise ValueError(f"Exports contain load areas other than {load_area}: {other}")
    # Overlapping exports are harmless; keep the last file's value for each hour.
    return (
        df.sort("period_end_utc", "source_file")
        .unique("period_end_utc", keep="last")
        .sort("period_end_utc")
    )


def missing_hours(df: pl.DataFrame) -> int:
    ts = df["period_end_utc"]
    full = pl.datetime_range(ts.min(), ts.max(), "1h", eager=True, time_zone="UTC")
    return len(full) - ts.n_unique()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not RAW_PJM_DIR.exists():
        log.warning("No %s; skipping PJM (optional cross-check input).", RAW_PJM_DIR)
        return
    df = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH)
    log.info(
        "PJM DOM: %d hours, %s -> %s, %d missing, %d unverified -> %s",
        df.height,
        df["period_end_utc"].min(),
        df["period_end_utc"].max(),
        missing_hours(df),
        (~df["is_verified"]).sum(),
        OUT_PATH,
    )


if __name__ == "__main__":
    main()
