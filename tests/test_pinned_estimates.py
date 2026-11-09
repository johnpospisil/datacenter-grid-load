"""Pinned headline estimates (Prompt 9). A silent change in data or method must fail here.

If a number moves on purpose (new data cut-off, a method change), update the pin in the
same commit and say why in the commit message. Values: data/outputs/growth_ranking.csv,
weather-adjusted growth from 2022 (FPL, SOCO: 2023) to the 12 months to 2026-10
(AZPS: to 2025-08), GAM counterfactual fitted on 2016-2019.
"""

from statistics import median

import polars as pl
import pytest

from glg.models import growth as g

pytestmark = pytest.mark.skipif(not g.RANKING_PATH.exists(), reason="run glg.models.growth")

TOL = 0.5  # percentage points

GROWTH_PCT = {
    "DOM": 23.63,
    "ERCO": 19.45,
    "AZPS": 16.58,
    "AZPS+SRP": 13.69,
    "BPAT": 13.19,
    "PJM": 6.40,
    "SOCO": 4.56,
    "FPL+SOCO": 3.75,
    "MISO": 4.11,
    "NYIS": -0.78,
    "ISNE": 0.21,
}
BEYOND_TREND_PCT = {"DOM": 18.29, "ERCO": 11.71, "PJM": 6.64}


@pytest.fixture(scope="module")
def rank():
    return {r["ba"]: r for r in pl.read_csv(g.RANKING_PATH).iter_rows(named=True)}


@pytest.mark.parametrize("ba, value", GROWTH_PCT.items())
def test_growth_since_2022(rank, ba, value):
    assert rank[ba]["growth_pct"] == pytest.approx(value, abs=TOL)


@pytest.mark.parametrize("ba, value", BEYOND_TREND_PCT.items())
def test_growth_beyond_baseline_trend(rank, ba, value):
    assert rank[ba]["beyond_trend_pct"] == pytest.approx(value, abs=TOL)


def test_model_families_agree_on_the_headline_bas(rank):
    for ba in ["DOM", "ERCO", "PJM"]:
        r = rank[ba]
        assert abs(r["growth_gbm_pct"] - r["growth_pct"]) < 1.5
        assert abs(r["growth_ddr_pct"] - r["growth_pct"]) < 1.5


def test_data_center_regions_outgrow_comparisons(rank):
    """The core claim's first half (brief: 'wrong if they grow no faster than comparisons')."""
    dc = ["DOM", "ERCO", "AZPS+SRP", "NEVP", "GCPD", "BPAT"]
    comp = ["MISO", "NYIS", "ISNE", "FPC", "CPLE", "CISO", "PACE", "FPL"]
    dc_med = median(rank[b]["growth_pct"] for b in dc)
    comp_med = median(rank[b]["growth_pct"] for b in comp)
    assert dc_med > 2 * comp_med
    assert dc_med - comp_med > 5
