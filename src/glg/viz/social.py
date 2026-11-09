"""Platform-sized chart images for the social posts (Prompt 20).

Each published chart is placed, unchanged and unstretched, on the canvas that wastes the
least space: 1600 x 900 (16:9) or 1200 x 1200 (1:1). Bluesky, X, Reddit and LinkedIn all
accept both, so one set serves every post. The 1200 x 630 link-preview card (Open Graph,
used by the report, the explorer, Hacker News unfurlers and LinkedIn link posts) is the
Dominion hero card on the light theme.

Run after `make figures`: python -m glg.viz.social  (writes report/social/)
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from glg.config import FIGURES_DIR
from glg.viz.theme import LIGHT

log = logging.getLogger(__name__)

SOCIAL_DIR = FIGURES_DIR.parent / "social"
CANVASES = {"16x9": (1600, 900), "1x1": (1200, 1200)}
OG_SIZE = (1200, 630)
PAD = 0.03  # share of the canvas kept clear on every side

# Thread order: one chart per post (posts.md refers to these file names).
THREAD = [
    "social/hero_card",
    "social/chart2_card",
    "chart8_dominion_metered",
    "hero",
    "chart4_supply_gap",
    "chart6_pjm_capacity",
    "robustness_forest",
]


def _fit(img: Image.Image, size: tuple[int, int], pad: float = PAD) -> Image.Image:
    w, h = size
    box_w, box_h = int(w * (1 - 2 * pad)), int(h * (1 - 2 * pad))
    scale = min(box_w / img.width, box_h / img.height)
    new = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGB", size, LIGHT.bg)
    canvas.paste(new, ((w - new.width) // 2, (h - new.height) // 2))
    return canvas


def best_canvas(img: Image.Image) -> str:
    """The canvas on which the chart fills the most area."""

    def fill(size):
        w, h = size
        s = min(w / img.width, h / img.height)
        return (img.width * s * img.height * s) / (w * h)

    return max(CANVASES, key=lambda k: fill(CANVASES[k]))


def build(out_dir: Path = SOCIAL_DIR) -> list[Path]:
    (out_dir / "thread").mkdir(parents=True, exist_ok=True)
    paths = []
    for i, stem in enumerate(THREAD, start=1):
        src = FIGURES_DIR / f"{stem}.png"
        img = Image.open(src).convert("RGB")
        name = Path(stem).name
        if img.size in CANVASES.values() or img.size == (1200, 675):
            out = img  # the cards are already drawn for social
        else:
            out = _fit(img, CANVASES[best_canvas(img)])
        p = out_dir / "thread" / f"{i:02d}_{name}.png"
        out.save(p, optimize=True)
        paths.append(p)
        log.info("%s %s", p.name, out.size)
    og = _fit(Image.open(FIGURES_DIR / "social" / "hero_card.png").convert("RGB"), OG_SIZE, 0.0)
    p = out_dir / "og_card.png"
    og.save(p, optimize=True)
    paths.append(p)
    return paths


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for p in build():
        print(p, Image.open(p).size)


if __name__ == "__main__":
    main()
