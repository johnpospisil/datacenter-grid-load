"""State total nonfarm employment, monthly, seasonally adjusted (BLS CES via FRED).

FRED series {ST}NA = "All Employees: Total Nonfarm in <state>", thousands of persons, SA,
from the BLS Current Employment Statistics (State and Metro Area) program. Public domain.
Used only for the optional economic-activity control (DATA.md, "Modelling table").

Run: python -m glg.ingest.employment
"""

import logging
from pathlib import Path

import polars as pl

from glg.config import RAW_DIR
from glg.ingest.http import download as fetch

log = logging.getLogger(__name__)

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
RAW_EMP_DIR = RAW_DIR / "employment"
STATES = (
    "AL AZ AR CA CO CT DE DC FL GA ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY"
).split()


def path(state: str) -> Path:
    return RAW_EMP_DIR / f"{state}NA.csv"


def download(refresh: bool = False) -> list[Path]:
    return [fetch(FRED.format(series=f"{s}NA"), path(s), None, refresh) for s in STATES]


def load() -> pl.DataFrame:
    """Long table: state, month (first day), employment in thousands."""
    parts = []
    for s in STATES:
        d = pl.read_csv(path(s), schema_overrides={f"{s}NA": pl.Utf8})
        parts.append(
            d.select(
                pl.lit(s).alias("state"),
                pl.col("observation_date").str.to_date().alias("month"),
                pl.col(f"{s}NA").cast(pl.Float64, strict=False).alias("emp_thousands"),
            ).drop_nulls()
        )
    return pl.concat(parts)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download()
    d = load()
    log.info("%d states, %s -> %s", d["state"].n_unique(), d["month"].min(), d["month"].max())


if __name__ == "__main__":
    main()
