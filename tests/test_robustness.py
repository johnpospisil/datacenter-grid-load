"""Robustness (Prompt 15): the claims that survive every specification, pinned."""

import polars as pl
import pytest

from glg.config import REFERENCE_DIR
from glg.models import robustness as rb

pytestmark = pytest.mark.skipif(not rb.GRID_PATH.exists(), reason="run glg.models.robustness")


@pytest.fixture(scope="module")
def grid():
    return pl.read_csv(rb.GRID_PATH)


@pytest.fixture(scope="module")
def med():
    return pl.read_csv(rb.MEDIANS_PATH)


def test_emp_gam_adds_only_the_employment_term():
    assert rb.EmpGam.features == [*rb.GamModel.features, "emp_index"]


def test_dominion_grows_fastest_under_every_spec(grid):
    h = grid.filter(pl.col("headline") & ~pl.col("ba").is_in(["AZPS", "SRP"]))
    for s in rb.SPECS:
        assert h.sort(s, descending=True)["ba"][0] == "DOM", s
    dom = grid.filter(pl.col("ba") == "DOM").row(0, named=True)
    assert dom["min_pct"] == pytest.approx(17.7, abs=0.5)  # with the employment control


def test_ercot_halves_with_the_employment_control(grid):
    erco = grid.filter(pl.col("ba") == "ERCO").row(0, named=True)
    assert erco["gam_2016_19"] == pytest.approx(19.45, abs=0.5)
    assert erco["gam_emp"] == pytest.approx(10.9, abs=0.5)


def test_core_comparison_survives_every_spec(med):
    dc6 = med.filter(pl.col("grouping") == "dc6_vs_d10")
    assert (dc6["gap_pts"] > 8).all() and (dc6["ratio"] > 3.5).all()
    pre = med.filter(pl.col("grouping") == "prompt2_roles")
    assert (pre["gap_pts"] > 1.5).all()  # weakest: employment control, 2.0 pts
    assert (pre.filter(pl.col("spec") != "gam_emp")["gap_pts"] > 4).all()


def test_dominion_dc_table_is_complete():
    d = pl.read_csv(REFERENCE_DIR / "dominion_dc_demand.csv")
    assert d["year"].to_list() == list(range(2013, 2027))
    assert d.filter(pl.col("year") == 2022)["coincident_mw"][0] == 2626
    assert (d["billing_mw"].diff().drop_nulls() > 0).all()
