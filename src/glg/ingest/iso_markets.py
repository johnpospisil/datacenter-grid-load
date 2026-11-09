"""Wholesale price context (Prompt 13). Descriptive only, never a causal model.

1. PJM capacity (RPM Base Residual Auction) clearing prices by delivery year, RTO and DOM:
   data/reference/pjm_bra_prices.csv, transcribed from PJM's BRA reports (cached in
   data/raw/pjm_rpm/ and re-checked against the PDFs by tests/test_prices.py). The DOM zone
   joined the auction in 2025/26; before that Dominion met its capacity obligation under
   the Fixed Resource Requirement (FRR) alternative, so earlier auction prices did not apply
   to its load.
2. PJM zonal real-time load-weighted average LMP by year, from the PJM Independent Market
   Monitor's (Monitoring Analytics) annual State of the Market reports, volume 2. Each
   report covers two years; the table layout differs between years (parsed below).
3. ERCOT real-time settlement point prices (hubs and load zones), annual and monthly means
   of 15-minute prices, via gridstatus (BSD-3-Clause), which reads ERCOT's public
   "Historical RTM Load Zone and Hub Prices" reports.
4. Bilateral day-ahead on-peak prices at western and PJM hubs (Palo Verde, Mid-C, PJM West)
   from EIA's Wholesale Electricity Market Data (ICE), annual mean of daily weighted averages.

Outputs (data/processed/): pjm_capacity_prices.parquet, wholesale_prices.parquet
(source, location, year, price_usd_mwh, measure).

Needs `pdftotext` (poppler) for the PJM PDFs: `brew install poppler`.

Run: python -m glg.ingest.iso_markets
"""

import logging
import re
import shutil
import subprocess

import pandas as pd
import polars as pl

from glg.config import PROCESSED_DIR, RAW_DIR, REFERENCE_DIR
from glg.ingest.http import download

log = logging.getLogger(__name__)

YEARS = range(2016, 2026)  # complete calendar years
BRA_PATH = REFERENCE_DIR / "pjm_bra_prices.csv"
CAPACITY_PATH = PROCESSED_DIR / "pjm_capacity_prices.parquet"
WHOLESALE_PATH = PROCESSED_DIR / "wholesale_prices.parquet"
PDF_MAGIC = b"%PDF"

RPM = "https://www.pjm.com/-/media/DotCom/markets-ops/rpm/rpm-auction-info"
BRA_REPORTS = {
    "2025/26": f"{RPM}/2025-2026/2025-2026-base-residual-auction-report.pdf",
    "2026/27": f"{RPM}/2026-2027/2026-2027-bra-report.pdf",
    "2027/28": f"{RPM}/2027-2028/2027-2028-bra-report.pdf",
    "2028/29": f"{RPM}/2028-2029/2028-2029-bra-results-report.pdf",
}
SOM = "https://www.monitoringanalytics.com/reports/PJM_State_of_the_Market"
SOM_REPORTS = {  # report year -> URL (covers report year and the year before)
    2017: f"{SOM}/2017/2017-som-pjm-volume2.pdf",
    2019: f"{SOM}/2019/2019-som-pjm-vol2.pdf",
    2021: f"{SOM}/2021/2021-som-pjm-vol2.pdf",
    2023: f"{SOM}/2023/2023-som-pjm-vol2.pdf",
    2025: f"{SOM}/2025/2025-som-pjm-vol2.pdf",
}
ZONE_NAMES = {  # older reports' zone names -> current IMM abbreviations
    "Dominion": "DOM", "ComEd": "COMED", "Pepco": "PEPCO", "AECO": "ACEC", "JCPL": "JCPLC",
    "RECO": "REC", "Met-Ed": "MEC", "PENELEC": "PE", "DLCO": "DUQ", "DEOK": "DUKE",
}  # fmt: skip
ERCOT_LOCATIONS = ["HB_HUBAVG", "HB_NORTH", "HB_HOUSTON", "HB_WEST", "HB_SOUTH", "LZ_NORTH"]
ICE = "https://www.eia.gov/electricity/wholesale/xls/archive/ice_electric-{y}final.{ext}"
ICE_HUBS = {
    "Palo Verde Peak": "Palo Verde",
    "Mid C Peak": "Mid-C",
    "PJM WH Real Time Peak": "PJM West",
}


# --------------------------------------------------------------------------- PJM capacity


def bra_reports() -> dict[str, object]:
    out = {}
    for dy, url in BRA_REPORTS.items():
        name = url.rsplit("/", 1)[1]
        out[dy] = download(url, RAW_DIR / "pjm_rpm" / name, PDF_MAGIC)
    return out


def capacity_prices() -> pl.DataFrame:
    return pl.read_csv(BRA_PATH).with_columns(
        pl.col("delivery_year").str.slice(0, 4).cast(pl.Int32).alias("start_year")
    )


# --------------------------------------------------------------------------- PJM zonal LMP


def pdf_text(path) -> str:
    if shutil.which("pdftotext") is None:
        raise RuntimeError("pdftotext not found: brew install poppler")
    return subprocess.run(
        ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
    ).stdout


TITLE = re.compile(r"Table \d+-\d+ Zonal real-time", re.IGNORECASE)


def _is_title(lines: list[str], i: int) -> bool:
    """The zonal load-weighted LMP table title (it may wrap over up to three lines)."""
    if not TITLE.search(lines[i]):
        return False
    w = " ".join(ln.strip() for ln in lines[i : i + 3]).lower().replace("- ", "-")
    return "weighted" in w and "average lmp" in w and "dlmp" not in w


MONEY = re.compile(r"\(?\$?\(?-?[\d,]+\.\d+\)?%?\)?")


def _money(tok: str) -> float:
    neg = tok.startswith("(") or "-" in tok
    v = float(re.sub(r"[^\d.]", "", tok))
    return -v if neg else v


def parse_zonal_lmp(text: str, report_year: int) -> pl.DataFrame:
    """Zonal real-time load-weighted LMP for (report_year - 1, report_year).

    Two layouts: the 'components' table (8 numbers per zone: LMP and 3 components for each
    year; the LMP is the 1st and 5th) and the older section-3 table (6 numbers: simple
    average y1, y2, % change, load-weighted y1, y2, % change; we take the 4th and 5th).
    """
    lines = text.splitlines()
    start = next(i for i in range(len(lines)) if _is_title(lines, i))
    rows = []
    for ln in lines[start + 1 : start + 80]:
        m = re.match(r"\s{0,12}([A-Za-z][A-Za-z\-]+)\s+(\(?\$.*)", ln)
        if not m:
            continue
        nums = [t for t in MONEY.findall(m.group(2)) if "%" not in t]
        zone = m.group(1)
        if len(nums) >= 8:
            y1, y2 = _money(nums[0]), _money(nums[4])
        elif len(nums) >= 4:
            y1, y2 = _money(nums[2]), _money(nums[3])
        else:
            continue
        rows += [(zone, report_year - 1, y1), (zone, report_year, y2)]
        if zone == "PJM":
            break
    df = pl.DataFrame(rows, schema=["zone_raw", "year", "price_usd_mwh"], orient="row")
    return df.with_columns(
        pl.col("zone_raw").replace(ZONE_NAMES).str.to_uppercase().alias("location")
    )


def pjm_zonal_lmp() -> pl.DataFrame:
    out = []
    for y, url in SOM_REPORTS.items():
        p = download(url, RAW_DIR / "pjm_imm" / f"{y}-som-pjm-vol2.pdf", PDF_MAGIC)
        out.append(parse_zonal_lmp(pdf_text(p), y))
    df = pl.concat(out).unique(["location", "year"], keep="last")
    return df.select(
        pl.lit("PJM IMM State of the Market").alias("source"),
        "location",
        pl.col("year").cast(pl.Int32),
        "price_usd_mwh",
        pl.lit("real-time load-weighted average LMP").alias("measure"),
    )


# --------------------------------------------------------------------------- ERCOT


def ercot_year(year: int, refresh: bool = False) -> pl.DataFrame:
    """Monthly mean real-time SPP per location for one year (cached)."""
    path = RAW_DIR / "ercot" / f"rtm_spp_monthly_{year}.parquet"
    if path.exists() and not refresh:
        return pl.read_parquet(path)
    import gridstatus

    df = pl.from_pandas(gridstatus.Ercot().get_rtm_spp(year)[["Interval Start", "Location", "SPP"]])
    m = (
        df.with_columns(pl.col("Interval Start").dt.truncate("1mo").dt.date().alias("month"))
        .group_by("Location", "month")
        .agg(pl.col("SPP").mean().alias("price_usd_mwh"), pl.len().alias("n_intervals"))
        .rename({"Location": "location"})
        .sort("location", "month")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    m.write_parquet(path)
    log.info("ERCOT %d: %d location-months", year, m.height)
    return m


def ercot_prices() -> pl.DataFrame:
    m = pl.concat([ercot_year(y) for y in YEARS])
    # annual mean of 15-min prices = interval-weighted mean of monthly means
    a = m.group_by("location", pl.col("month").dt.year().alias("year")).agg(
        (pl.col("price_usd_mwh") * pl.col("n_intervals")).sum() / pl.col("n_intervals").sum()
    )
    return a.filter(pl.col("location").is_in(ERCOT_LOCATIONS)).select(
        pl.lit("ERCOT (via gridstatus)").alias("source"),
        "location",
        pl.col("year").cast(pl.Int32),
        "price_usd_mwh",
        pl.lit("real-time settlement point price, simple average").alias("measure"),
    )


# --------------------------------------------------------------------------- EIA ICE


def ice_prices() -> pl.DataFrame:
    out = []
    for y in YEARS:
        ext = "xls" if y <= 2016 else "xlsx"
        url = ICE.format(y=y, ext=ext)
        p = download(url, RAW_DIR / "eia_ice" / f"ice_electric-{y}final.{ext}")
        d = pd.read_excel(p)
        d.columns = [str(c).replace("\n", " ").strip() for c in d.columns]
        d = d[d["Price hub"].isin(ICE_HUBS)]
        for hub, g in d.groupby("Price hub"):
            out.append(
                {
                    "location": ICE_HUBS[hub],
                    "year": y,
                    "price_usd_mwh": float(g["Wtd avg price $/MWh"].astype(float).mean()),
                }
            )
    return pl.DataFrame(out).select(
        pl.lit("EIA Wholesale Electricity Market Data (ICE)").alias("source"),
        "location",
        pl.col("year").cast(pl.Int32),
        "price_usd_mwh",
        pl.lit("day-ahead on-peak bilateral, mean of daily weighted averages").alias("measure"),
    )


def run() -> tuple[pl.DataFrame, pl.DataFrame]:
    bra_reports()
    cap = capacity_prices()
    w = pl.concat([pjm_zonal_lmp(), ercot_prices(), ice_prices()])
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    cap.write_parquet(CAPACITY_PATH)
    w.write_parquet(WHOLESALE_PATH)
    with pl.Config(tbl_rows=40, tbl_cols=12):
        print(
            w.filter(pl.col("location").is_in(["DOM", "PJM", "HB_HUBAVG", "Palo Verde", "Mid-C"]))
            .pivot("year", index="location", values="price_usd_mwh")
            .with_columns(pl.col(pl.Float64).round(1))
        )
    return cap, w


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()


if __name__ == "__main__":
    main()
