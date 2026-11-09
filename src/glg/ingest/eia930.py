"""EIA-930 hourly demand, forecast, generation and interchange by BA and subregion.

Source: EIA's six-month bulk CSV files (Grid Monitor "sixMonthFiles"). We use these rather
than API v2 because the API only serves data from 2019-01-01, while the bulk files go back
to 2015-07 (BALANCE) and 2018-07 (SUBREGION) and carry EIA's imputed/adjusted demand columns.

Usage:
    python -m glg.ingest.eia930              # download missing + recent files, rebuild parquet
    python -m glg.ingest.eia930 --refresh    # re-download every file
"""

import argparse
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import polars as pl
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from glg.config import INTERIM_DIR, RAW_DIR, REFERENCE_DIR

log = logging.getLogger(__name__)

BULK_URL = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/{name}.csv"
REFERENCE_URL = "https://www.eia.gov/electricity/930-content/EIA930_Reference_Tables.xlsx"

RAW_EIA930 = RAW_DIR / "eia930"
REFERENCE_XLSX = RAW_DIR / "eia930_reference" / "EIA930_Reference_Tables.xlsx"
BALANCE_PARQUET = INTERIM_DIR / "eia930_hourly.parquet"
SUBREGION_PARQUET = INTERIM_DIR / "eia930_subregion_hourly.parquet"
BA_METADATA_CSV = REFERENCE_DIR / "ba_metadata.csv"
SUBREGION_METADATA_CSV = REFERENCE_DIR / "subregion_metadata.csv"

BALANCE_START = (2015, 2)  # (year, half): 2015 Jul-Dec
SUBREGION_START = (2018, 2)
# EIA revises recent data, so files whose period ended within this window are re-downloaded.
REFRESH_WINDOW = timedelta(days=120)

BALANCE_COLUMNS = {
    "Balancing Authority": "ba",
    "Region": "region",
    "UTC Time at End of Hour": "period_end_utc",
    "Local Time at End of Hour": "period_end_local",
    "Demand Forecast (MW)": "demand_forecast",
    "Demand (MW)": "demand",
    "Demand (MW) (Imputed)": "demand_imputed",
    "Demand (MW) (Adjusted)": "demand_adjusted",
    "Net Generation (MW)": "net_generation",
    "Net Generation (MW) (Adjusted)": "net_generation_adjusted",
    "Total Interchange (MW)": "total_interchange",
    "Total Interchange (MW) (Adjusted)": "total_interchange_adjusted",
}
SUBREGION_COLUMNS = {
    "Balancing Authority": "ba",
    "Sub-Region": "subregion",
    "UTC Time at End of Hour": "period_end_utc",
    "Local Time at End of Hour": "period_end_local",
    "Demand (MW)": "demand",
}
TIMESTAMP_FORMAT = "%m/%d/%Y %I:%M:%S %p"

# EIA's "Time Zone" is the zone the BA reports local time in. "Arizona" means Mountain
# Standard Time all year. For calendar features we need civil time where people live.
EIA_TZ_TO_IANA = {
    "Eastern": "America/New_York",
    "Central": "America/Chicago",
    "Mountain": "America/Denver",
    "Arizona": "America/Phoenix",
    "Pacific": "America/Los_Angeles",
}
# BAs that report in "Arizona" time but serve areas that observe DST.
CIVIL_TZ_OVERRIDES = {
    "EPE": "America/Denver",  # El Paso Electric: west Texas / southern New Mexico
    "PNM": "America/Denver",  # Public Service Co. of New Mexico
    "WACM": "America/Denver",  # WAPA Rocky Mountain: Colorado, Wyoming
}


def six_month_periods(start: tuple[int, int], today: date) -> list[tuple[int, int]]:
    """All (year, half) pairs from start through the half containing today."""
    year, half = start
    last = (today.year, 1 if today.month <= 6 else 2)
    periods = []
    while (year, half) <= last:
        periods.append((year, half))
        year, half = (year, 2) if half == 1 else (year + 1, 1)
    return periods


def file_name(kind: str, year: int, half: int) -> str:
    return f"EIA930_{kind}_{year}_{'Jan_Jun' if half == 1 else 'Jul_Dec'}"


def period_end(year: int, half: int) -> date:
    return date(year, 6, 30) if half == 1 else date(year, 12, 31)


def needs_download(path: Path, year: int, half: int, today: date, refresh: bool) -> bool:
    if refresh or not path.exists():
        return True
    return period_end(year, half) >= today - REFRESH_WINDOW


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, max=60), reraise=True)
def _download(url: str, dest: Path, expect_prefix: bytes) -> None:
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    # A missing file returns EIA's HTML 404 page with status 200, so check the content.
    if not resp.content.lstrip(b"\xef\xbb\xbf").startswith(expect_prefix):
        raise ValueError(f"Unexpected content from {url}: {resp.content[:80]!r}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(resp.content)
    tmp.replace(dest)


def download_bulk_files(today: date, refresh: bool = False) -> dict[str, list[Path]]:
    RAW_EIA930.mkdir(parents=True, exist_ok=True)
    files: dict[str, list[Path]] = {"BALANCE": [], "SUBREGION": []}
    for kind, start in (("BALANCE", BALANCE_START), ("SUBREGION", SUBREGION_START)):
        for year, half in six_month_periods(start, today):
            name = file_name(kind, year, half)
            path = RAW_EIA930 / f"{name}.csv"
            if needs_download(path, year, half, today, refresh):
                log.info("Downloading %s", name)
                _download(BULK_URL.format(name=name), path, b'"Balancing Authority"')
            files[kind].append(path)
    return files


def download_reference(refresh: bool = False) -> Path:
    REFERENCE_XLSX.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not REFERENCE_XLSX.exists():
        log.info("Downloading EIA-930 reference tables")
        _download(REFERENCE_URL, REFERENCE_XLSX, b"PK")  # xlsx is a zip archive
    return REFERENCE_XLSX


def _to_number(col: str) -> pl.Expr:
    return pl.col(col).str.replace_all(",", "").cast(pl.Float64, strict=True)


def read_bulk_file(path: Path, columns: dict[str, str]) -> pl.DataFrame:
    """Read one six-month CSV, keeping the core columns with typed values."""
    df = pl.read_csv(path, infer_schema=False).select(list(columns)).rename(columns)
    value_cols = [c for c in df.columns if c not in ("ba", "region", "subregion")]
    value_cols = [c for c in value_cols if not c.startswith("period_end")]
    return df.with_columns(
        pl.col("period_end_utc")
        .str.strptime(pl.Datetime("us"), TIMESTAMP_FORMAT)
        .dt.replace_time_zone("UTC"),
        pl.col("period_end_local").str.strptime(pl.Datetime("us"), TIMESTAMP_FORMAT),
        *[_to_number(c) for c in value_cols],
        source_file=pl.lit(path.name),
    )


def build_parquet(
    paths: list[Path], columns: dict[str, str], keys: list[str], out: Path
) -> pl.DataFrame:
    df = pl.concat([read_bulk_file(p, columns) for p in paths], how="vertical")
    n_dupes = df.height - df.unique(subset=keys).height
    if n_dupes:
        # Six-month files shouldn't overlap; keep the row from the newest file if they do.
        log.warning("%d duplicate %s rows; keeping the latest file's row", n_dupes, keys)
        df = df.sort("source_file").unique(subset=keys, keep="last")
    df = df.sort(keys)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out)
    log.info("Wrote %s (%d rows)", out, df.height)
    return df


def _iso_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="%m/%d/%Y").dt.date


def build_ba_metadata(balance: pl.DataFrame, subregion: pl.DataFrame) -> pd.DataFrame:
    ref = pd.read_excel(REFERENCE_XLSX, sheet_name="BAs")
    meta = pd.DataFrame(
        {
            "ba": ref["BA Code"],
            "name": ref["BA Name"],
            "region": ref["Region/Country Code"],
            "eia_time_zone": ref["Time Zone"],
            "us_ba": ref["U.S. BA"].eq("Yes"),
            "generation_only": ref["Generation Only BA"].eq("Yes"),
            "has_subregions": ref["Demand by BA Subregion"].eq("Yes"),
            "active": ref["Active BA"].eq("Yes"),
            "activation_date": _iso_date(ref["Activation Date"]),
            "retirement_date": _iso_date(ref["Retirement Date"]),
        }
    )
    meta["tz_reporting"] = meta["eia_time_zone"].map(EIA_TZ_TO_IANA)
    meta["tz_civil"] = meta["ba"].map(CIVIL_TZ_OVERRIDES).fillna(meta["tz_reporting"])

    coverage = (
        balance.group_by("ba")
        .agg(
            first_hour_utc=pl.col("period_end_utc").min(),
            last_hour_utc=pl.col("period_end_utc").max(),
            n_hours=pl.len(),
            n_demand=pl.col("demand").is_not_null().sum(),
        )
        .to_pandas()
    )
    meta = meta.merge(coverage, on="ba", how="left")
    # "Subregion parent" = this BA reports subregion demand in the files we downloaded.
    meta["n_subregions"] = (
        meta["ba"]
        .map(
            subregion.group_by("ba")
            .agg(pl.col("subregion").n_unique())
            .to_pandas()
            .set_index("ba")["subregion"]
        )
        .fillna(0)
        .astype(int)
    )
    return meta.sort_values("ba").reset_index(drop=True)


def build_subregion_metadata(subregion: pl.DataFrame) -> pd.DataFrame:
    ref = pd.read_excel(REFERENCE_XLSX, sheet_name="BA Subregions")
    meta = pd.DataFrame(
        {
            "ba": ref["BA Code"],
            "subregion": ref["BA Subregion Code"].astype(str),
            "name": ref["BA Subregion Name"],
            "active": ref["Active Subregion"].eq("Yes"),
            "activation_date": _iso_date(ref["Activation Date"]),
            "retirement_date": _iso_date(ref["Retirement Date"]),
        }
    )
    coverage = (
        subregion.group_by("ba", "subregion")
        .agg(
            first_hour_utc=pl.col("period_end_utc").min(),
            last_hour_utc=pl.col("period_end_utc").max(),
            n_hours=pl.len(),
        )
        .to_pandas()
    )
    return meta.merge(coverage, on=["ba", "subregion"], how="outer").sort_values(
        ["ba", "subregion"]
    )


def log_summary(balance: pl.DataFrame) -> None:
    summary = (
        balance.group_by("ba")
        .agg(
            first=pl.col("period_end_utc").min(),
            last=pl.col("period_end_utc").max(),
            rows=pl.len(),
            null_demand=pl.col("demand").is_null().sum(),
        )
        .with_columns(
            expected=((pl.col("last") - pl.col("first")).dt.total_hours() + 1),
        )
        .with_columns(missing_hours=pl.col("expected") - pl.col("rows"))
        .sort("ba")
    )
    with pl.Config(tbl_rows=-1, tbl_width_chars=160):
        log.info("EIA-930 BALANCE coverage by BA:\n%s", summary)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-download every file")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    today = datetime.now(UTC).date()
    files = download_bulk_files(today, refresh=args.refresh)
    download_reference(refresh=args.refresh)

    balance = build_parquet(
        files["BALANCE"], BALANCE_COLUMNS, ["ba", "period_end_utc"], BALANCE_PARQUET
    )
    subregion = build_parquet(
        files["SUBREGION"],
        SUBREGION_COLUMNS,
        ["ba", "subregion", "period_end_utc"],
        SUBREGION_PARQUET,
    )

    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    build_ba_metadata(balance, subregion).to_csv(BA_METADATA_CSV, index=False)
    build_subregion_metadata(subregion).to_csv(SUBREGION_METADATA_CSV, index=False)
    log.info("Wrote %s and %s", BA_METADATA_CSV, SUBREGION_METADATA_CSV)
    log_summary(balance)


if __name__ == "__main__":
    main()
