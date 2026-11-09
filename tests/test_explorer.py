"""The static region explorer's data export (Prompt 18)."""

import json
import math
from pathlib import Path

import pytest

from glg.models import growth as g
from glg.viz import explorer as E

pytestmark = pytest.mark.skipif(not g.RANKING_PATH.exists(), reason="run glg.models.growth")


@pytest.fixture(scope="module")
def idx():
    return E.build()


def _doc(slug: str) -> dict:
    return json.loads((E.DATA_DIR / "series" / f"{slug}.json").read_text())


def test_every_offered_series_has_a_file(idx):
    slugs = {r["slug"] for r in idx["regions"]}
    assert len(slugs) == len(E.SERIES)
    for s in slugs:
        assert (E.DATA_DIR / "series" / f"{s}.json").exists()


def test_appendix_and_sensitivity_series_are_not_offered(idx):
    codes = {r["code"] for r in idx["regions"]}
    assert not codes & {"DOPD", "PSCO", "LGEE", "AZPS_full"}  # decision D10


def test_headline_numbers_match_the_pins(idx):
    r = {x["code"]: x for x in idx["regions"]}
    assert r["DOM"]["growth_pct"] == pytest.approx(23.63, abs=0.5)
    assert r["ERCO"]["growth_pct"] == pytest.approx(19.45, abs=0.5)
    assert r["AZPS+SRP"]["growth_pct"] == pytest.approx(13.69, abs=0.5)
    assert r["AZPS+SRP"]["last12_to"] == "2025-08"
    assert idx["dc_median_pct"] > 2 * idx["comp_median_pct"]


def test_monthly_series_is_consistent(idx):
    d = _doc("dom")
    m = d["monthly"]
    months = [r["m"] for r in m]
    assert months == sorted(months) and len(months) == len(set(months))
    assert months[-1] == idx["cutoff"][:7]
    last = m[-1]
    assert 135 < last["i"] < 146 and last["lo"] <= last["i"] <= last["hi"]
    assert last["n"] > 100 and last["d"] > 100


def test_supply_retirements_are_positive_magnitudes():
    d = _doc("dom")
    coal = sum(r["ret_coal"] for r in d["supply"]["years"])
    assert coal > 1000  # Chesterfield and others, exported as positive MW retired
    for r in d["supply"]["years"]:
        assert all(r[k] >= 0 for k in r if k.startswith(("add_", "ret_")))


def test_combined_series_sums_its_parts_in_the_queue():
    both = _doc("azps-srp")["queue"]["active_gw"]
    parts = _doc("azps")["queue"]["active_gw"] + _doc("srp")["queue"]["active_gw"]
    assert both == pytest.approx(parts, abs=0.2)


def test_caveats_travel_with_the_numbers():
    assert any("41%" in n for n in _doc("erco")["notes"])
    assert any("August 2025" in n for n in _doc("azps-srp")["notes"])
    assert any("no utility disclosure" in n.lower() for n in _doc("bpat")["notes"])


def _floats(x):
    if isinstance(x, float):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _floats(v)
    elif isinstance(x, list):
        for v in x:
            yield from _floats(v)


def test_json_is_finite_and_compact():
    total = 0
    for p in E.DATA_DIR.rglob("*.json"):
        total += p.stat().st_size
        assert all(math.isfinite(v) for v in _floats(json.loads(p.read_text()))), p
    assert total < 1_000_000


def test_nothing_private_is_exported():
    src = Path(E.__file__).read_text()
    # the only mention of the private folder is the docstring saying it is never read
    assert src.count("private/") == 1 and '"private"' not in src
    for p in E.DATA_DIR.rglob("*.json"):
        assert "hrl_load_metered" not in p.read_text()


def test_page_references_only_pinned_cdn_versions():
    html = (E.APP_DIR / "index.html").read_text()
    assert "d3@7.9.0" in html and "@observablehq/plot@0.6.16" in html
    assert "@latest" not in html
