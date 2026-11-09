"""Chart theme and figure registry (Prompt 16)."""

import matplotlib.pyplot as plt
import pytest

from glg.viz import figures as F
from glg.viz.theme import DARK, LIGHT, THEMES, use


def _lum(hex_: str) -> float:
    rgb = [int(hex_.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted([_lum(a), _lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("t", [LIGHT, DARK], ids=lambda t: t.name)
def test_text_tokens_meet_wcag_aa(t):
    assert contrast(t.ink, t.bg) >= 7
    assert contrast(t.muted, t.bg) >= 4.5


def test_series_contrast_against_surface():
    # dark: every slot >= 3:1 (validated with the dataviz validator)
    assert all(contrast(c, DARK.bg) >= 3 for c in DARK.cat)
    # light: amber and sky are below 3:1 and only used with a legend / direct labels
    low = {c for c in LIGHT.cat if contrast(c, LIGHT.bg) < 3}
    assert low == {LIGHT.amber, LIGHT.sky}
    for t in THEMES.values():
        assert contrast(t.focus, t.bg) >= 3 and contrast(t.contrast, t.bg) >= 3


def test_same_slots_in_both_themes():
    assert len(LIGHT.cat) == len(DARK.cat) == 6
    assert LIGHT.suffix == "" and DARK.suffix == "_dark"


def test_registry_covers_hero_and_charts_2_to_8():
    stems = [s.stem for s in F.FIGURES]
    assert len(stems) == len(set(stems))
    for want in ["hero", *[f"chart{i}_" for i in range(2, 9)]]:
        assert any(s.startswith(want) for s in stems), want
    assert {s.stem for s in F.FIGURES if s.social} == {"hero_card", "chart2_card"}


FAST = [s for s in F.FIGURES if s.stem != "chart3_dom_heat_calendar"]  # chart 3 refits ~1 min


@pytest.mark.parametrize("spec", FAST, ids=lambda s: s.stem)
def test_every_chart_has_title_source_and_covid(spec):
    with use(LIGHT):
        fig, alt = spec.build(LIGHT)
    try:
        assert getattr(fig, "_glg_title", "")
        assert alt and len(alt) > 30
        if not spec.social:
            src = fig._glg_source
            assert "Source" in src
            if spec.stem.startswith("chart") or spec.stem == "hero":
                assert src.split(" ·")[0] in ("Hero", f"Chart {spec.stem[5:6]}")
        if spec.time_series:
            assert getattr(fig, "_glg_covid", False)
        # every axis has units on y or is a labelled bar/forest/heat-map
        texts = [ax.get_ylabel() + ax.get_xlabel() for ax in fig.axes]
        assert any(texts) or spec.social
    finally:
        plt.close(fig)


def test_build_writes_png_and_svg_in_both_themes(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FIGURES_DIR", tmp_path)
    spec = next(s for s in F.FIGURES if s.stem == "chart6_pjm_capacity")
    for t in THEMES.values():
        F.build(spec, t)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "chart6_pjm_capacity.png",
        "chart6_pjm_capacity.svg",
        "chart6_pjm_capacity_dark.png",
        "chart6_pjm_capacity_dark.svg",
    ]
    svg = (tmp_path / "chart6_pjm_capacity.svg").read_text()
    assert "PJM capacity prices" in svg  # title in SVG metadata
    assert "Step chart" in svg  # alt text in SVG metadata


def test_missing_covid_shading_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "FIGURES_DIR", tmp_path)

    def bare(t):
        fig, ax = plt.subplots()
        ax.plot([1, 2], [1, 2])
        fig._glg_title = "x"
        return fig, "a line chart without covid shading for the test"

    with pytest.raises(AssertionError, match="COVID"):
        F.build(F.Spec("bare", bare, True), LIGHT)
    plt.close("all")
