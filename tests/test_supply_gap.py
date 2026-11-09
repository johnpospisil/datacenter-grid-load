"""Supply side (Prompt 11): 860M parsing rules, month-weighted capacity, energy conversion."""

from datetime import date

import polars as pl
import pytest

from glg.ingest import eia860m
from glg.models import supply_gap as sg


def test_every_cf_group_has_a_chart_group():
    assert set(eia860m.TECH_CF.values()) - {"pet"} <= set(eia860m.GROUP)
    assert {"pet_st", "pet_gt", "pet_ic"} <= set(eia860m.GROUP)


def test_candidates_go_back_from_this_month():
    c = eia860m.candidates(date(2026, 1, 15))
    assert c[0][0] == "january_generator2026.xlsx"
    assert c[2][0] == "december_generator2025.xlsx"


@pytest.mark.parametrize(
    "raw, key",
    [("Virginia Beach City", "virginia beach"), ("St. Mary's", "st marys"), ("Loudoun", "loudoun")],
)
def test_county_names_are_normalised(raw, key):
    out = pl.DataFrame({"c": [raw]}).select(sg._county_key("c"))["c"][0]
    assert out == key


def months(op, ret, start, end):
    df = pl.DataFrame({"op": [op], "ret": [ret]}, schema={"op": pl.Date, "ret": pl.Date})
    return df.select(sg.months_online(pl.col("op"), pl.col("ret"), start, end))[0, 0]


def test_months_online():
    y22 = (date(2022, 1, 1), date(2022, 12, 1))
    assert months(date(2022, 7, 1), None, *y22) == 6  # online from July
    assert months(date(2010, 1, 1), date(2022, 3, 1), *y22) == 2  # retired in March
    assert months(date(2024, 1, 1), None, *y22) == 0
    assert months(date(2010, 1, 1), None, date(2025, 11, 1), date(2026, 10, 1)) == 12


def _since(units: list[dict]) -> dict:
    gs = pl.DataFrame(units, schema_overrides={"op_month": pl.Date, "ret_month": pl.Date})
    cf = pl.DataFrame({"cf_group": ["solar_pv", "storage", "coal"], "cf": [0.25, 0.0, 0.5]})
    ranking = pl.DataFrame(
        {
            "ba": ["SYN"],
            "from_year": [2022],
            "last12_from": ["2025-11-01"],
            "last12_to": ["2026-10-01"],
            "growth_pct": [10.0],
            "mw_added": [100.0],
            "twh_per_yr_added": [0.8766],
        }
    )
    annual = pl.DataFrame({"ba": ["SYN"], "year": [2022], "actual_mw": [1000.0]})
    return sg.since(gs, cf, ranking, annual).row(0, named=True)


def unit(group, cf_group, mw, op, ret=None):
    return {"series": "SYN", "group": group, "cf_group": cf_group, "summer_mw": mw,
            "op_month": op, "ret_month": ret}  # fmt: skip


def test_new_solar_counts_its_expected_energy():
    r = _since([unit("Solar", "solar_pv", 100.0, date(2024, 1, 1))])
    assert r["new_supply_twh"] == pytest.approx(100 * 0.25 * 8766 / 1e6)
    assert r["new_supply_mw"] == pytest.approx(100.0)
    assert r["supply_pct"] == pytest.approx(100 * r["new_supply_twh"] / 8.766)


def test_unit_added_mid_2022_counts_half():
    r = _since([unit("Solar", "solar_pv", 100.0, date(2022, 7, 1))])
    assert r["new_supply_mw"] == pytest.approx(50.0)


def test_retirement_and_storage():
    r = _since(
        [
            unit("Coal", "coal", 200.0, date(1990, 1, 1), date(2023, 1, 1)),
            unit("Storage", "storage", 300.0, date(2024, 1, 1)),
        ]
    )
    assert r["new_supply_twh"] == pytest.approx(-200 * 0.5 * 8766 / 1e6)
    assert r["new_storage_mw"] == pytest.approx(300.0)
    assert r["twh_coal"] < 0


# --------------------------------------------------------------------------- built outputs

needs_outputs = pytest.mark.skipif(not sg.SINCE_PATH.exists(), reason="run supply_gap")


@needs_outputs
def test_dominion_supply_gap_is_pinned():
    """Pinned (Prompt 11): DOM demand +28 TWh/yr since 2022, local net new supply ~1.4."""
    s = {r["series"]: r for r in pl.read_csv(sg.SINCE_PATH).iter_rows(named=True)}
    assert s["DOM"]["demand_twh_added"] == pytest.approx(28.1, abs=0.5)
    assert s["DOM"]["new_supply_twh"] == pytest.approx(1.36, abs=0.5)
    assert s["DOM"]["gap_pct"] < -20
    assert s["ERCO"]["new_supply_twh"] == pytest.approx(65.1, abs=2)


@needs_outputs
def test_capacity_changes_have_known_groups():
    c = pl.read_parquet(sg.CHANGES_PATH)
    assert set(c["group"].unique()) <= set(sg.GROUPS)
    assert c["year"].min() >= sg.FIRST_YEAR
    assert c.filter(pl.col("event") == "retired")["mw"].max() <= 0
