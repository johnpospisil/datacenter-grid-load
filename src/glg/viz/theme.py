"""Shared chart theme: colour tokens, light/dark variants and the chart frame.

Every published chart is drawn twice, once per theme, from the same code. Colours are
tokens on a `Theme`; chart code never hard-codes a hex value.

Palette: Okabe-Ito, in a fixed slot order (amber, magenta, sky, green, vermillion, blue)
plus a neutral gray for "Other"/reference marks. Checked with the dataviz skill's
`validate_palette.js`:

- light (on #ffffff): passes lightness band, chroma, CVD and normal-vision separation on
  *all* pairs; amber and sky are below 3:1 contrast, so they are only used where the
  chart also has a legend or direct labels.
- dark (on #16181c): the same hues stepped into the dark lightness band (L 0.48-0.67);
  passes every check on *adjacent* pairs, with >= 3:1 contrast for all six. Two
  non-adjacent pairs are weak (amber/vermillion for deuteranopes, sky/blue), so the
  stacked queue chart keeps them apart in the stack order and folds nuclear (tiny) into
  "Other" instead of using slot 6.

Text always uses ink tokens (ink, muted), never a series colour.

Layout: titles, subtitles, legends and source lines sit at fixed distances (in inches)
from the figure edges, so every chart has the same frame.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

COVID_START = date(2020, 3, 1)
COVID_END = date(2021, 12, 31)

FONT_STACK = ["Source Sans Pro", "Lato", "Helvetica Neue", "DejaVu Sans"]
PNG_DPI = 200


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str  # chart surface
    ink: str  # primary text, axis lines for zero/reference
    muted: str  # secondary text: subtitles, tick labels, source line
    faint: str  # gridlines
    rule: str  # reference lines (index = 100, zero)
    covid: str  # COVID shading
    other: str  # gray series: "Other", comparison reference
    cat: tuple[str, ...]  # categorical slots, fixed order
    diverging: tuple[str, ...]  # cool pole -> neutral -> warm pole
    suffix: str = ""
    extras: dict = field(default_factory=dict)

    # named roles (by slot, so the same entity keeps its colour in both themes)
    @property
    def amber(self) -> str:
        return self.cat[0]

    @property
    def magenta(self) -> str:
        return self.cat[1]

    @property
    def sky(self) -> str:
        return self.cat[2]

    @property
    def green(self) -> str:
        return self.cat[3]

    @property
    def vermillion(self) -> str:
        return self.cat[4]

    @property
    def blue(self) -> str:
        return self.cat[5]

    # semantic aliases used across charts
    @property
    def focus(self) -> str:
        """The series the chart is about (weather-adjusted demand, night load, RTO price)."""
        return self.blue

    @property
    def contrast(self) -> str:
        """The series it is compared with (trend, afternoon load, Dominion zone)."""
        return self.vermillion

    def cmap_diverging(self) -> LinearSegmentedColormap:
        return LinearSegmentedColormap.from_list(f"div_{self.name}", list(self.diverging))


LIGHT = Theme(
    name="light",
    bg="#ffffff",
    ink="#1f2328",
    muted="#57606a",
    faint="#eaeef2",
    rule="#c4cbd3",
    covid="#eef1f4",
    other="#8c959f",
    cat=("#E69F00", "#CC79A7", "#56B4E9", "#009E73", "#D55E00", "#0072B2"),
    diverging=("#0072B2", "#9ecae1", "#eceef0", "#f4a582", "#D55E00"),
)

DARK = Theme(
    name="dark",
    bg="#16181c",
    ink="#e6edf3",
    muted="#9da7b3",
    faint="#262b32",
    rule="#4a525c",
    covid="#22262d",
    other="#7d8590",
    cat=("#BF8400", "#B8699A", "#3E9AD2", "#009E73", "#DB6A2E", "#3F87D4"),
    diverging=("#6aaef0", "#2f5f91", "#2b2f36", "#94512c", "#f08a50"),
    suffix="_dark",
)

THEMES = {"light": LIGHT, "dark": DARK}


def rc(t: Theme) -> dict:
    """matplotlib rcParams for a theme."""
    return {
        "figure.facecolor": t.bg,
        "axes.facecolor": t.bg,
        "savefig.facecolor": t.bg,
        "axes.edgecolor": t.rule,
        "axes.labelcolor": t.muted,
        "axes.titlecolor": t.ink,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": False,
        "grid.color": t.faint,
        "grid.linewidth": 0.6,
        "xtick.color": t.rule,
        "ytick.color": t.rule,
        "xtick.labelcolor": t.muted,
        "ytick.labelcolor": t.muted,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "axes.labelsize": 9,
        "axes.titlesize": 10.5,
        "axes.titlelocation": "left",
        "legend.frameon": False,
        "legend.fontsize": 9,
        "legend.labelcolor": t.ink,
        "text.color": t.ink,
        "font.family": "sans-serif",
        "font.sans-serif": FONT_STACK,
        "lines.solid_capstyle": "round",
        "svg.fonttype": "path",
        "svg.hashsalt": "glg",  # deterministic SVG ids
        "pdf.fonttype": 42,
    }


@contextmanager
def use(t: Theme):
    with mpl.rc_context(rc(t)):
        yield t


def style_axes(ax, t: Theme, grid: str | None = "y") -> None:
    ax.spines[["top", "right"]].set_visible(False)
    if grid:
        ax.grid(axis=grid, color=t.faint, lw=0.6, zorder=0)
    ax.set_axisbelow(True)


def shade_covid(ax, t: Theme, years: bool = False, label: bool = False) -> None:
    """Shade 2020-03 to 2021-12 (excluded from every baseline). `years`: numeric x-axis."""
    if years:
        x0, x1 = 2020 + 2 / 12, 2022.0
    else:
        x0, x1 = COVID_START, COVID_END
    ax.axvspan(x0, x1, color=t.covid, lw=0, zorder=0)
    ax.figure._glg_covid = True
    if label:
        ax.text(
            x0,
            1.0,
            " COVID",
            transform=ax.get_xaxis_transform(),
            fontsize=7.5,
            color=t.muted,
            va="top",
            ha="left",
        )


def _frac(fig, inches: float, axis: str = "y") -> float:
    w, h = fig.get_size_inches()
    return inches / (h if axis == "y" else w)


TITLE_IN, SUB_IN, LEGEND_IN = 0.34, 0.6, 0.86  # baselines from the top edge
LEFT_IN = 0.14


def frame(
    fig,
    t: Theme,
    title: str,
    subtitle: str | None,
    source: str,
    legend: bool = False,
    source_lines: int | None = None,
) -> tuple[float, float, float, float]:
    """Title, subtitle and source line at fixed positions. Returns the content rect
    (left, bottom, right, top in figure fractions) for tight_layout / add_axes."""
    x = _frac(fig, LEFT_IN, "x")
    fig.text(
        x,
        1 - _frac(fig, TITLE_IN),
        title,
        fontsize=14,
        fontweight="bold",
        color=t.ink,
        ha="left",
        va="baseline",
    )
    top_in = SUB_IN - 0.1
    if subtitle:
        fig.text(
            x,
            1 - _frac(fig, SUB_IN),
            subtitle,
            fontsize=10,
            color=t.muted,
            ha="left",
            va="baseline",
        )
        top_in = SUB_IN + 0.12
    if legend:
        top_in = LEGEND_IN + 0.14
    n = source_lines or (source.count("\n") + 1)
    fig.text(
        x,
        _frac(fig, 0.1),
        source,
        fontsize=7.5,
        color=t.muted,
        ha="left",
        va="bottom",
        linespacing=1.35,
    )
    bottom_in = 0.14 + 0.15 * n
    fig._glg_title = title
    fig._glg_source = source
    return (0.0, _frac(fig, bottom_in), 1.0, 1 - _frac(fig, top_in))


def legend_row(fig, t: Theme, handles, ncol: int | None = None, fontsize: float = 9) -> None:
    """A one-row legend under the subtitle, left-aligned with the title."""
    fig.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(_frac(fig, LEFT_IN - 0.08, "x"), 1 - _frac(fig, LEGEND_IN - 0.1)),
        ncol=ncol or len(handles),
        frameon=False,
        fontsize=fontsize,
        handlelength=1.8,
        columnspacing=1.6,
        labelcolor=t.ink,
    )


def save(fig, t: Theme, stem: str, out_dir: Path, alt: str = "") -> list[Path]:
    """Write <stem><suffix>.png and .svg. `alt` goes into the SVG/PNG metadata."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    title = getattr(fig, "_glg_title", stem)
    for ext in ("png", "svg"):
        p = out_dir / f"{stem}{t.suffix}.{ext}"
        meta = {"Title": title, "Description": alt}
        if ext == "svg":
            meta["Date"] = None  # reproducible output
        kw = {"dpi": PNG_DPI} if ext == "png" else {}
        fig.savefig(p, metadata=meta, **kw)
        paths.append(p)
    plt.close(fig)
    return paths
