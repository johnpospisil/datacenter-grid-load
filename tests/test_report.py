"""The Quarto report's numbers and figures (Prompt 17).

The report (`report/index.qmd`) takes every result from `glg.report`, so these tests check
that module against the same pins as test_pinned_estimates.py, and check the .qmd for
headline numbers typed in by hand (which would go stale silently).
"""

import re
from pathlib import Path

import pytest

from glg import report as R
from glg.config import FIGURES_DIR
from glg.models import growth as g

pytestmark = pytest.mark.skipif(not g.RANKING_PATH.exists(), reason="run glg.models.growth")

QMD = Path(__file__).resolve().parents[1] / "report" / "index.qmd"


@pytest.fixture(scope="module")
def n():
    return R.numbers()


def _f(s: str) -> float:
    return float(re.sub(r"[^\d.\-]", "", s.replace("−", "-")))


def test_headline_numbers_match_the_pins(n):
    assert _f(n.dom_g) == pytest.approx(23.63, abs=0.5)
    assert _f(n.erco_g) == pytest.approx(19.45, abs=0.5)
    assert _f(n.azps_srp_g) == pytest.approx(13.69, abs=0.5)
    assert _f(n.dc_med) == pytest.approx(13.4, abs=0.5)
    assert _f(n.comp_med) == pytest.approx(3.3, abs=0.5)


def test_ranges_quoted_in_the_summary(n):
    lo, hi = (float(x) for x in n.gap_all.split("–"))
    assert 1 <= lo <= 3 and 9 <= hi <= 11  # "2–10 points, depending on grouping"
    assert n.dc_share_22_25 == "60%" and n.dc_share_19_25 == "73%"
    assert int(n.dom_sup_share.rstrip("%")) <= 5


def test_negative_numbers_use_a_true_minus(n):
    assert n.nyis_g.startswith("−")
    assert "-" not in n.nyis_ci


def test_no_headline_number_is_typed_into_the_report():
    text = QMD.read_text()
    for value in ["23.6", "19.5", "13.7", "13.4", "3.2 GW", "+1,373", "+2,301"]:
        assert value not in text, f"{value} is hard-coded in index.qmd; use N.<name>"


def test_every_inline_name_exists(n):
    names = set(re.findall(r"`\{python\} N\.(\w+)`", QMD.read_text()))
    assert names, "no inline numbers found"
    missing = names - set(n.d)
    assert not missing, missing


def test_every_figure_in_the_report_exists_in_both_themes():
    stems = re.findall(r'show\("(\w+)"', QMD.read_text())
    assert {"hero", "chart2_rising_floor", "chart7_validation", "chart8_dominion_metered"} <= set(
        stems
    )
    for s in stems:
        assert (FIGURES_DIR / f"{s}.png").exists() and (FIGURES_DIR / f"{s}_dark.png").exists()
        html = R.fig(s)
        assert "light-content" in html and "dark-content" in html
        assert 'alt=""' not in html and len(R.alt_text(s)) > 30


def test_missing_figure_fails_loudly():
    with pytest.raises(FileNotFoundError):
        R.fig("no_such_chart")


def test_tables_render_as_markdown():
    for t in [R.growth_table(), R.robustness_table(), R.medians_table(), R.validation_table()]:
        lines = t.splitlines()
        assert lines[0].startswith("| ") and set(lines[1]) <= set("|:- ")
        assert len({line.count("|") for line in lines}) == 1  # rectangular
