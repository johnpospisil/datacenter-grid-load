"""Platform-sized social images (Prompt 20)."""

import pytest
from PIL import Image

from glg.viz import social as S

ALLOWED = {(1200, 675), (1600, 900), (1200, 1200)}


@pytest.fixture(scope="module")
def paths(tmp_path_factory):
    if not (S.FIGURES_DIR / "hero.png").exists():
        pytest.skip("run make figures")
    return S.build(tmp_path_factory.mktemp("social"))


def test_one_image_per_thread_post_at_platform_sizes(paths):
    thread = [p for p in paths if p.parent.name == "thread"]
    assert len(thread) == len(S.THREAD) == 7
    for p in thread:
        assert Image.open(p).size in ALLOWED, p.name


def test_link_preview_card_is_1200_by_630(paths):
    og = next(p for p in paths if p.name == "og_card.png")
    assert Image.open(og).size == S.OG_SIZE


def test_charts_are_not_stretched():
    img = Image.new("RGB", (2000, 1000), "white")
    out = S._fit(img, (1600, 900))
    assert out.size == (1600, 900)
    assert S.best_canvas(Image.new("RGB", (2200, 2200))) == "1x1"
    assert S.best_canvas(Image.new("RGB", (3200, 1720))) == "16x9"
