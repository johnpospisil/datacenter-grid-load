"""NOAA Integrated Surface Database (ISD / Global Hourly) airport observations.

Used only as a sanity check on ERA5 (decision D1): a few airports, a few years.
Files come from NOAA's open-data bucket (noaa-global-hourly-pds), one CSV per station-year.

Run: python -m glg.ingest.isd
"""

import logging
from pathlib import Path

import polars as pl

from glg.config import RAW_DIR
from glg.ingest.http import download as fetch

log = logging.getLogger(__name__)

BASE = "https://noaa-global-hourly-pds.s3.amazonaws.com"
RAW_ISD_DIR = RAW_DIR / "isd"
# station id -> (airport, BA it sits in, county FIPS)
STATIONS = {
    "72403093738": ("Washington Dulles (IAD)", "DOM", "51107"),
    "72278023183": ("Phoenix Sky Harbor (PHX)", "SRP", "04013"),
    "72259003927": ("Dallas/Fort Worth (DFW)", "ERCO", "48439"),
    "72202012839": ("Miami (MIA)", "FPL", "12086"),
}
# Other Phoenix-area airports, to tell a Sky Harbor sensor drift from real regional warming.
PHOENIX_CHECK = {
    "72278403184": "Phoenix Deer Valley (DVT)",
    "72278523111": "Luke AFB (LUF)",
    "72274953128": "Chandler (CHD)",
    "72278623104": "Mesa Gateway (IWA)",
    "72278753126": "Glendale (GEU)",
    "72278803186": "Goodyear (GYR)",
    "72278903192": "Scottsdale (SDL)",
}
YEARS = tuple(range(2016, 2026))  # baseline + post-2022, to check for drift
# ISD quality codes that mean "passed" (ISD format document, section on quality codes).
GOOD_QC = ["0", "1", "4", "5", "9", "A", "C", "I", "M", "P", "R", "U"]


def path(station: str, year: int) -> Path:
    return RAW_ISD_DIR / f"{station}_{year}.csv"


def download(refresh: bool = False) -> list[Path]:
    out = []
    for s in [*STATIONS, *PHOENIX_CHECK]:
        for y in YEARS:
            try:
                out.append(fetch(f"{BASE}/{y}/{s}.csv", path(s, y), None, refresh))
            except Exception as exc:  # noqa: BLE001 - some small airports miss years
                log.warning("No ISD file for %s %d: %s", s, y, exc)
    return out


def _value(col: str) -> pl.Expr:
    """'+0078,1' -> 7.8 deg C, null if missing (+9999) or failed QC."""
    parts = pl.col(col).str.split(",")
    v, qc = parts.list.get(0), parts.list.get(1)
    return (
        pl.when((v != "+9999") & qc.is_in(GOOD_QC))
        .then(v.cast(pl.Float64, strict=False) / 10)
        .alias(col.lower() + "_c")
    )


def read_hourly(station: str, year: int) -> pl.DataFrame:
    """Routine hourly METAR reports (FM-15), stamped to the nearest whole UTC hour."""
    d = pl.read_csv(
        path(station, year),
        columns=["STATION", "DATE", "REPORT_TYPE", "TMP", "DEW"],
        infer_schema_length=0,
    )
    d = d.filter(pl.col("REPORT_TYPE").str.strip_chars() == "FM-15").with_columns(
        pl.col("DATE").str.to_datetime("%Y-%m-%dT%H:%M:%S", time_zone="UTC", time_unit="us"),
        _value("TMP"),
        _value("DEW"),
    )
    d = d.with_columns(pl.col("DATE").dt.round("1h").alias("time_utc"))
    # Keep the report closest to the hour when there are several.
    d = d.with_columns((pl.col("DATE") - pl.col("time_utc")).abs().alias("off")).sort("off")
    return (
        d.unique("time_utc", keep="first")
        .select(pl.lit(station).alias("station"), "time_utc", "tmp_c", "dew_c")
        .sort("time_utc")
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download()


if __name__ == "__main__":
    main()
