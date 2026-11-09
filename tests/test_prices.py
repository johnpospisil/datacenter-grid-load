# ruff: noqa: E501  (fixtures reproduce PDF table layouts verbatim)
"""Price context (Prompt 13): capacity table checked against PJM's PDFs, LMP table parsing."""

import re
import shutil

import polars as pl
import pytest

from glg.config import RAW_DIR
from glg.ingest import eia861
from glg.ingest import iso_markets as im

COMPONENTS = """
      Table 11-5 Zonal real-time load-weighted average LMP components (Dollars per MWh): 2024 and 2025
                                      2024                                           2025
                     Real-Time   Energy Congestion      Loss         Real-Time   Energy Congestion      Loss
      COMED             $27.69   $33.55    ($4.36)   ($1.50)            $39.75   $50.26     ($8.01)  ($2.51)
      DOM               $39.07   $33.50       $4.92    $0.65            $65.58   $50.31     $14.04     $1.23
      PJM               $33.74   $33.64       $0.07    $0.03            $50.73   $50.55       $0.13    $0.04
"""
SECTION3 = """
      Table 3-47 Zonal real-time and real-time, load-          some other text in a side column
      weighted, average LMP (Dollars per MWh): 2018 and
      2019
       Zone                             2018       2019    Change                      2018       2019   Change
       ComEd                           $28.57    $23.53    (17.6%)                   $30.05      $24.72  (17.7%)
       Dominion                        $38.91    $25.16    (35.3%)                   $43.76      $27.71  (36.7%)
       PJM                             $35.75    $26.02    (27.2%)                   $38.24      $27.32  (28.6%)
"""


def as_dict(df: pl.DataFrame) -> dict:
    return {(r["location"], r["year"]): r["price_usd_mwh"] for r in df.iter_rows(named=True)}


def test_parse_components_layout():
    d = as_dict(im.parse_zonal_lmp(COMPONENTS, 2025))
    assert d[("DOM", 2025)] == 65.58 and d[("DOM", 2024)] == 39.07
    assert d[("COMED", 2024)] == 27.69 and d[("PJM", 2025)] == 50.73


def test_parse_older_layout_takes_the_load_weighted_columns():
    d = as_dict(im.parse_zonal_lmp(SECTION3, 2019))
    assert d[("DOM", 2018)] == 43.76 and d[("DOM", 2019)] == 27.71  # not the simple average
    assert d[("COMED", 2019)] == 24.72 and d[("PJM", 2018)] == 38.24


def test_negative_money():
    assert im._money("($4.36)") == -4.36 and im._money("$14.04") == 14.04


def test_capacity_table_rules():
    c = im.capacity_prices()
    assert c["start_year"].to_list() == list(range(2018, 2029))
    assert not c.filter(pl.col("start_year") < 2025)["dom_in_rpm"].any()  # FRR until 2024/25
    capped = c.filter(pl.col("cap").is_not_null())
    assert (capped["rto_price"] <= capped["cap"]).all()


PDFS = RAW_DIR / "pjm_rpm"


@pytest.mark.skipif(
    shutil.which("pdftotext") is None or not (PDFS / "2028-2029-bra-results-report.pdf").exists(),
    reason="needs pdftotext and the cached BRA reports",
)
def test_capacity_table_matches_pjm_reports():
    """Every transcribed price appears in PJM's reports (RTO history: 2028/29 report, Table 2)."""
    c = im.capacity_prices()
    hist = im.pdf_text(PDFS / "2028-2029-bra-results-report.pdf")
    for r in c.iter_rows(named=True):
        dy = f"{r['start_year']}/{str(r['start_year'] + 1)[2:]}"
        line = next(ln for ln in hist.splitlines() if ln.strip().startswith(dy))
        assert f"${r['rto_price']:.2f}" in line, dy
    texts = {p.name: im.pdf_text(p) for p in PDFS.glob("*.pdf")}
    for r in c.filter(pl.col("dom_in_rpm")).iter_rows(named=True):
        row = re.compile(
            rf"^\s*DOM\s+[\d,.]+\s+[\d,.]+\s+\$[\d.]+\s+\$[\d.]+\s+\${r['dom_price']:.2f}", re.M
        )
        assert any(row.search(t) for t in texts.values()), r["delivery_year"]


def test_retail_price_is_revenue_over_sales():
    if not eia861.RETAIL_PATH.exists():
        pytest.skip("run glg.ingest.eia861")
    r = pl.read_parquet(eia861.RETAIL_PATH)
    us25 = r.filter(
        (pl.col("state") == "US") & (pl.col("sector") == "ALL") & (pl.col("period") == "2025")
    )
    assert us25["price_cents_kwh"][0] == pytest.approx(
        13.56, abs=0.02
    )  # EIA's published 2025 price
    assert us25["price_real_2025"][0] == pytest.approx(us25["price_cents_kwh"][0])
    assert r.filter(pl.col("trailing")).height > 0
