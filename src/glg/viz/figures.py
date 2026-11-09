# ruff: noqa: E501  (chart titles and labels are long strings)
"""Report figures: the hero chart and charts 2-8, plus context charts and social cards.

Every figure is a function of a `Theme` that returns (figure, alt text), drawn from the
processed outputs only (no model fitting). `make figures` runs `main()`, which writes
each figure as PNG + SVG in the light and dark themes to `report/figures/`, and the
social cards to `report/figures/social/`.

Published DOM numbers use EIA-930 only (decision D3): nothing here reads
`data/outputs/private/`.

Run: python -m glg.viz.figures [stem ...]
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import polars as pl  # noqa: E402
from matplotlib.colors import TwoSlopeNorm  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from glg.config import FIGURES_DIR, OUTPUTS_DIR, REFERENCE_DIR  # noqa: E402
from glg.viz.theme import THEMES, Theme, frame, legend_row, save, shade_covid, style_axes, use  # noqa: E402

log = logging.getLogger(__name__)

SOCIAL_DIR = FIGURES_DIR / "social"

DC = ["DOM", "ERCO", "AZPS+SRP", "NEVP", "GCPD", "BPAT"]
COMPARISONS = ["MISO", "NYIS", "ISNE", "FPC", "CPLE", "CISO", "PACE", "FPL"]  # D10
PHOENIX_END = date(2025, 8, 1)  # Phoenix headline window ends 2025-08 (DATA.md)

NAMES = {
    "DOM": "Dominion (VA)",
    "ERCO": "ERCOT (TX)",
    "AZPS+SRP": "Phoenix (APS + SRP)",
    "AZPS": "Arizona Public Service",
    "SRP": "Salt River Project",
    "BPAT": "Bonneville (NW)",
    "NEVP": "NV Energy",
    "GCPD": "Grant County PUD (WA)",
    "PJM": "PJM (all)",
    "SWPP": "SPP",
    "MISO": "MISO",
    "TVA": "TVA",
    "SOCO": "Southern Company",
    "FPL+SOCO": "FPL + Southern",
    "DUK": "Duke Carolinas",
    "CPLE": "Duke Progress East",
    "FPC": "Duke Florida",
    "PACE": "PacifiCorp East",
    "CISO": "CAISO",
    "ISNE": "ISO New England",
    "NYIS": "NYISO",
    "FPL": "Florida Power & Light",
}

# Short in-panel versions of data/reference/hero_annotations.csv labels (by ref).
HERO_SHORT = {
    "U01": "Dominion's metered data\ncenters peak at 4.0 GW (2025)",
    "U02": "4.8 GW (Aug 2026)",
    "U19": "ERCOT: 9.0 GW of large loads\napproved, 3.9 GW observed peak",
    "U21": "New data-center energization\npaused (Aug 2026)",
    "U14": "APS stops new data-center\ncommitments (2024)",
    "U23": "Grant PUD: data centers\nare 37% of load (2025)",
    "U11": "Southern: data-center\nsales +55% y/y",
}

SRC_DEMAND = (
    "EIA-930 hourly demand (DOM: PJM subregion), ERA5 temperature. "
    "Demand ÷ a 2016–19 weather-and-calendar model (GAM)."
)


def _years_axis(ax, step: int = 2) -> None:
    ax.xaxis.set_major_locator(mdates.YearLocator(step))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))


def _end_label(ax, t: Theme, x, y, text: str, dy: float = 0, size: float = 9) -> None:
    ax.annotate(
        text,
        (x, y),
        xytext=(4, dy),
        textcoords="offset points",
        fontsize=size,
        color=t.ink,
        va="center",
    )


def _spread(values: dict, gap: float) -> dict:
    """Nudge label positions apart so direct labels don't collide."""
    order = sorted(values, key=values.get)
    ys = [values[k] for k in order]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + gap)
    return dict(zip(order, ys, strict=False))


# --------------------------------------------------------------------------- data


def _growth():
    from glg.models import growth as g

    return (
        g,
        pl.read_csv(g.RANKING_PATH),
        pl.read_parquet(g.MONTHLY_PATH),
        pl.read_csv(g.ANNUAL_PATH),
    )


def _trend_index(g, monthly, annual, ba, months):
    """The BA's own 2016-19 residual trend, extended, in index terms (2019 = 100),
    evaluated at the centre of each trailing 12-month window."""
    m = monthly.filter(
        pl.col("ba") == ba,
        pl.col("month").dt.year().is_between(2016, 2019),
        pl.col("n_hours") >= g.MIN_MONTH_HOURS,
    )
    b, _, b0 = g.slope(m)
    r19 = 1 + annual.filter(pl.col("ba") == ba, pl.col("year") == 2019)["resid_pct"][0] / 100
    tt = np.array([d.year + (d.month - 0.5) / 12 - 0.5 for d in months])
    return 100 * (1 + (b0 + b * tt) / 100) / r19


def _comparison_median(monthly):
    ref_bas = [b for b in COMPARISONS if b != "FPL"]  # FPL's index steps with Gulf Power
    roll = monthly.filter(pl.col("index_12m").is_not_null())
    return (
        roll.filter(pl.col("ba").is_in(ref_bas))
        .group_by("month")
        .agg(pl.col("index_12m").median(), pl.len())
        .filter(pl.col("len") == len(ref_bas))
        .sort("month")
    )


def hero_bas(rank: pl.DataFrame, n: int = 10) -> list[str]:
    """Top-n headline series by growth since 2022 (Phoenix shown combined)."""
    return (
        rank.filter(pl.col("headline"), ~pl.col("ba").is_in(["AZPS", "SRP"]))
        .sort("growth_pct", descending=True)
        .head(n)["ba"]
        .to_list()
    )


# --------------------------------------------------------------------------- hero


def _hero_panel(ax, t, ba, g, rank, monthly, annual, ref, ann, big=False):
    roll = monthly.filter(pl.col("index_12m").is_not_null())
    d = roll.filter(pl.col("ba") == ba).sort("month")
    m = d["month"].to_list()
    shade_covid(ax, t)
    ax.axhline(100, color=t.rule, lw=0.8, zorder=1)
    ax.plot(ref["month"], ref["index_12m"], color=t.other, lw=1.3, zorder=2)
    later = [x for x in m if x.year >= 2020]
    ax.plot(
        later,
        _trend_index(g, monthly, annual, ba, later),
        color=t.contrast,
        lw=1.3,
        ls="--",
        zorder=2,
    )
    cut = PHOENIX_END if ba in ("AZPS", "AZPS+SRP") else None
    ok = d if cut is None else d.filter(pl.col("month") <= cut)
    ax.fill_between(
        ok["month"],
        ok["index_12m_lo"],
        ok["index_12m_hi"],
        color=t.focus,
        alpha=0.25,
        lw=0,
        zorder=3,
    )
    ax.plot(ok["month"], ok["index_12m"], color=t.focus, lw=2.6 if big else 2, zorder=4)
    if cut is not None:
        dis = d.filter(pl.col("month") >= cut)
        ax.plot(dis["month"], dis["index_12m"], color=t.focus, lw=1.2, ls=":", zorder=4)
        ax.text(
            dis["month"][-1],
            dis["index_12m"][-1] + 2.5,
            "disputed",
            fontsize=7,
            color=t.muted,
            ha="right",
        )
    last = ok.row(-1, named=True)
    _end_label(
        ax, t, last["month"], last["index_12m"], f"{last['index_12m']:.0f}", size=12 if big else 9
    )
    # utility disclosures (Prompt 14), never more than two per panel
    rows = ann.filter(pl.col("series") == ba).sort("date", descending=True).head(2)
    for i, a in enumerate(rows.iter_rows(named=True)):
        when = date.fromisoformat(a["date"]).replace(day=1)
        on = d.filter(pl.col("month") == when)
        if on.is_empty():
            continue
        y = on["index_12m"][0]
        ax.scatter([when], [y], s=22, color=t.bg, edgecolor=t.ink, lw=1.1, zorder=6)
        ax.annotate(
            HERO_SHORT.get(a["ref"], a["label"]),
            (when, y),
            xytext=(0.03, 0.95 - 0.16 * i),
            textcoords="axes fraction",
            fontsize=8.5 if big else 7,
            color=t.ink,
            va="top",
            ha="left",
            linespacing=1.15,
            arrowprops=dict(
                arrowstyle="-",
                color=t.muted,
                lw=0.6,
                shrinkA=2,
                shrinkB=3,
                connectionstyle="arc3,rad=0",
            ),
            zorder=5,
        )
    r = rank.filter(pl.col("ba") == ba).row(0, named=True)
    role = "" if ba in DC or r["role"] == "focus" else " · comparison"
    ax.set_title(
        f"{NAMES.get(ba, ba)}{role}\n+{r['growth_pct']:.0f}% since {r['from_year']}",
        fontsize=12 if big else 9.5,
        color=t.ink,
        linespacing=1.25,
    )
    _years_axis(ax, 2)
    style_axes(ax, t)


def hero(t: Theme):
    g, rank, monthly, annual = _growth()
    ann = pl.read_csv(REFERENCE_DIR / "hero_annotations.csv")
    ref = _comparison_median(monthly)
    top = hero_bas(rank)
    fig, axes = plt.subplots(2, 5, figsize=(16, 8.6), sharex=True, sharey=True)
    for ax, ba in zip(axes.flat, top, strict=False):
        _hero_panel(ax, t, ba, g, rank, monthly, annual, ref, ann)
    hi = max(monthly.filter(pl.col("ba").is_in(top))["index_12m_hi"].drop_nulls().max(), 140)
    axes[0, 0].set_ylim(85, hi + 12)
    for ax in axes[:, 0]:
        ax.set_ylabel("Weather-adjusted demand\n(12-month, 2019 = 100)")
    rect = frame(
        fig,
        t,
        "Weather-adjusted electricity demand: the ten fastest-growing grid regions since 2022",
        "Demand after removing weather and calendar effects, trailing 12 months, 2019 = 100. "
        "Dominion's grew by a quarter; comparison regions (gray) by about 3%.",
        f"Hero · Source: {SRC_DEMAND} 95% band: jackknife over baseline years + block bootstrap.\n"
        "Utility figures: Dominion (PJM Load Analysis Subcommittee, 2026-09), ERCOT large-load "
        "reports, APS, Grant PUD (data/reference/utility_reported_load.csv). "
        "Phoenix after 2025-08 disputed (EIA-930 vs FERC 714).",
        legend=True,
    )
    legend_row(
        fig,
        t,
        [
            Line2D([], [], color=t.focus, lw=2, label="Weather-adjusted demand (95% band)"),
            Line2D(
                [],
                [],
                color=t.contrast,
                lw=1.3,
                ls="--",
                label="Region's own 2016–19 trend, extended",
            ),
            Line2D([], [], color=t.other, lw=1.3, label="Median of comparison regions"),
            Line2D(
                [],
                [],
                marker="o",
                ls="",
                markerfacecolor=t.bg,
                markeredgecolor=t.ink,
                label="Utility or grid-operator disclosure",
            ),
            Patch(color=t.covid, label="COVID (excluded from baseline)"),
        ],
    )
    fig.tight_layout(rect=rect, h_pad=1.6)
    last = monthly.filter(pl.col("ba") == "DOM", pl.col("index_12m").is_not_null()).sort("month")
    alt = (
        f"Ten small line charts of weather-adjusted demand indexed to 2019 = 100. Dominion "
        f"rises to {last['index_12m'][-1]:.0f}; the comparison median stays near 100–105."
    )
    return fig, alt


def card_hero(t: Theme):
    """Social card (1200 x 675 px): Dominion alone, with the comparison median."""
    g, rank, monthly, annual = _growth()
    ann = pl.read_csv(REFERENCE_DIR / "hero_annotations.csv")
    ref = _comparison_median(monthly)
    fig, ax = plt.subplots(figsize=(6, 3.375))
    _hero_panel(ax, t, "DOM", g, rank, monthly, annual, ref, ann.head(0), big=True)
    ax.set_title("")
    r = rank.filter(pl.col("ba") == "DOM").row(0, named=True)
    fig.text(
        0.025,
        0.93,
        f"Dominion's grid (Virginia): +{r['growth_pct']:.0f}% since 2022",
        fontsize=13,
        fontweight="bold",
        color=t.ink,
        va="top",
    )
    fig.text(
        0.025,
        0.835,
        "Electricity demand after removing weather. Comparison regions (gray): about +3%.",
        fontsize=9,
        color=t.muted,
        va="top",
    )
    fig.text(
        0.025,
        0.02,
        "Dominion (PJM DOM zone), 12-month index, 2019 = 100. "
        "EIA-930, ERA5, 2016–19 GAM. Dashed: own 2016–19 trend.",
        fontsize=6.5,
        color=t.muted,
    )
    ax.set_ylim(90, 150)
    ax.tick_params(labelsize=7.5)
    fig.subplots_adjust(left=0.07, right=0.94, top=0.74, bottom=0.12)
    fig._glg_title = "Dominion's grid since 2022"
    return fig, "Dominion's weather-adjusted demand index rising from 100 in 2019 to about 141."


# --------------------------------------------------------------------------- chart 2


def _ls():
    from glg.models import load_shape as ls

    return ls, pl.read_csv(ls.DECOMP_PATH), pl.read_parquet(ls.MONTHLY_PATH)


def _floor_panel(ax, t, ba, monthly, dec_row, big=False):
    ends = {}
    for band, col, lab in [("night", t.focus, "Night"), ("afternoon", t.contrast, "Afternoon")]:
        m = monthly.filter(
            (pl.col("ba") == ba)
            & (pl.col("band") == band)
            & pl.col("index_12m").is_not_nan()
            & (pl.col("month") >= date(2016, 12, 1))
            & (pl.col("month") <= (PHOENIX_END if ba == "AZPS+SRP" else date(2100, 1, 1)))
        ).sort("month")
        ax.plot(m["month"], m["index_12m"], color=col, lw=2.6 if big else 2, zorder=3)
        txt = f"{lab} {m['index_12m'][-1]:.0f}" if big else f"{m['index_12m'][-1]:.0f}"
        ends[txt] = (m["month"][-1], m["index_12m"][-1])
    pos = _spread({k: v[1] for k, v in ends.items()}, 4.5)
    for txt, (x, _) in ends.items():
        _end_label(ax, t, x, pos[txt], txt, size=10 if big else 9)
    shade_covid(ax, t)
    ax.axhline(100, color=t.rule, lw=0.8, zorder=1)
    _years_axis(ax, 2)
    ax.set_xlim(date(2016, 12, 1), date(2027, 6, 1) if big else date(2027, 3, 1))
    style_axes(ax, t)


def chart2(t: Theme):
    ls, dec, monthly = _ls()
    rows = [("DOM", "CPLE"), ("ERCO", "FPC"), ("AZPS+SRP", "CISO")]
    d0 = {r["ba"]: r for r in dec.iter_rows(named=True)}
    fig, axes = plt.subplots(3, 2, figsize=(11, 11), sharey=True, sharex=True)
    for (dc, comp), axrow in zip(rows, axes, strict=False):
        for ba, ax in zip((dc, comp), axrow, strict=False):
            _floor_panel(ax, t, ba, monthly, d0[ba])
            r = d0[ba]
            role = "data-center region" if ba == dc else "comparison"
            ax.set_title(
                f"{NAMES[ba]} · {role}\nsince 2022: +{r['d_night_mw']:,.0f} MW at night, "
                f"+{r['d_afternoon_mw']:,.0f} MW in the afternoon",
                fontsize=9.5,
            )
    for ax in axes[:, 0]:
        ax.set_ylabel("Index, 2019 = 100")
    rect = frame(
        fig,
        t,
        "The rising floor: data-center regions add nearly as many MW at night as in the afternoon",
        "Weather-adjusted demand index by time of day, trailing 12 months. "
        "Comparison regions with similar climate move far less.",
        f"Chart 2 · Source: {SRC_DEMAND}\nLocal time; night = 1–5 am, afternoon = 2–7 pm. "
        "Phoenix shown to 2025-08, where its headline window ends. The same MW added at every "
        "hour raise the night index more, because the night base is smaller.",
        legend=True,
    )
    legend_row(
        fig,
        t,
        [
            Line2D([], [], color=t.focus, lw=2, label="Night, 1–5 am local"),
            Line2D([], [], color=t.contrast, lw=2, label="Afternoon, 2–7 pm local"),
            Patch(color=t.covid, label="COVID (excluded from baseline)"),
        ],
    )
    fig.tight_layout(rect=rect, h_pad=1.8)
    r = d0["DOM"]
    alt = (
        "Six line charts of night vs afternoon weather-adjusted demand. Dominion adds "
        f"{r['d_night_mw']:,.0f} MW at night and {r['d_afternoon_mw']:,.0f} MW in the afternoon; "
        "its comparison region barely moves."
    )
    return fig, alt


def card_chart2(t: Theme):
    """Social card: Dominion's night vs afternoon index."""
    ls, dec, monthly = _ls()
    r = {x["ba"]: x for x in dec.iter_rows(named=True)}["DOM"]
    fig, ax = plt.subplots(figsize=(6, 3.375))
    _floor_panel(ax, t, "DOM", monthly, r, big=True)
    fig.text(
        0.025,
        0.93,
        "Dominion's new load runs around the clock",
        fontsize=13,
        fontweight="bold",
        color=t.ink,
        va="top",
    )
    fig.text(
        0.025,
        0.835,
        f"Since 2022: +{r['d_night_mw']:,.0f} MW at night, "
        f"+{r['d_afternoon_mw']:,.0f} MW in the afternoon (weather-adjusted).",
        fontsize=9,
        color=t.muted,
        va="top",
    )
    fig.text(
        0.025,
        0.02,
        "Dominion (PJM DOM zone), 12-month index, 2019 = 100; night 1–5 am, "
        "afternoon 2–7 pm. EIA-930, ERA5, 2016–19 GAM.",
        fontsize=6.5,
        color=t.muted,
    )
    ax.tick_params(labelsize=7.5)
    fig.subplots_adjust(left=0.07, right=0.84, top=0.74, bottom=0.12)
    fig._glg_title = "Dominion's new load runs around the clock"
    return fig, "Dominion's night-time and afternoon demand indices both rising since 2022."


# --------------------------------------------------------------------------- chart 3


def chart3(t: Theme):
    ls, _, _ = _ls()
    cf = {s.name: s.df for s in ls.build_series(ls.load_hourly()) if s.name in ("DOM", "CPLE")}
    norm = TwoSlopeNorm(vmin=-3000, vcenter=0, vmax=6000)
    start = date(2018, 9, 17)  # DOM clean EIA-930 data begins 2018-09-18
    fig = plt.figure(figsize=(11, 7.2))
    rect = frame(
        fig,
        t,
        "Dominion's extra load arrives at every hour of the day",
        "Weekly average of actual minus modelled demand, by local hour. Same MW scale for both "
        "regions.",
        "Chart 3 · Source: EIA-930 hourly demand (DOM: PJM subregion), ERA5 temperature. "
        "Model: GAM fitted on 2016–19 (DOM: 2018-09 to 2019-12).\nCells with fewer than 4 hours "
        "left blank. Dotted lines: start of COVID (2020-03) and of the growth window (2022).",
    )
    _, b, _, top = rect
    h = (top - b - 0.12) / 2
    axes = [
        fig.add_axes([0.08, b + h + 0.1, 0.78, h - 0.02]),
        fig.add_axes([0.08, b + 0.04, 0.78, h - 0.02]),
    ]
    cax = fig.add_axes([0.885, b + 0.12, 0.014, 2 * h - 0.12])
    names = {"DOM": "Dominion (PJM DOM zone)", "CPLE": "Duke Energy Progress East · comparison"}
    for ax, ba in zip(axes, ["DOM", "CPLE"], strict=False):
        d = (
            cf[ba]
            .filter(pl.col("day") >= start)
            .with_columns(pl.col("day").dt.truncate("1w").alias("week"))
            .group_by("week", "local_hour")
            .agg(pl.col("resid").mean().alias("mw"), pl.len().alias("n"))
            .filter(pl.col("n") >= 4)
        )
        weeks = pl.date_range(d["week"].min(), d["week"].max(), "1w", eager=True)
        grid = (
            pl.DataFrame({"week": weeks})
            .join(d.pivot("local_hour", index="week", values="mw"), on="week", how="left")
            .sort("week")
        )
        z = np.array(
            [
                grid[str(hh)].to_numpy()
                if str(hh) in grid.columns
                else np.full(grid.height, np.nan)
                for hh in range(24)
            ],
            dtype=float,
        )
        x = mdates.date2num(grid["week"].to_list())
        im = ax.pcolormesh(
            np.append(x, x[-1] + 7),
            np.arange(25),
            z,
            cmap=t.cmap_diverging(),
            norm=norm,
            shading="flat",
            rasterized=True,
        )
        ax.set_yticks([0, 6, 12, 18, 24], ["midnight", "6 am", "noon", "6 pm", "midnight"])
        ax.set_title(names[ba], fontsize=10.5, pad=14)
        for dt, lab in [(date(2020, 3, 1), "COVID"), (date(2022, 1, 1), "2022")]:
            ax.axvline(mdates.date2num(dt), color=t.ink, lw=0.7, ls=":")
            ax.text(mdates.date2num(dt) + 12, 24.4, lab, fontsize=7.5, color=t.muted, va="bottom")
        ax.set_xlim(mdates.date2num(start), x[-1] + 7)
        _years_axis(ax, 1)
        ax.spines[["top", "right"]].set_visible(False)
    fig._glg_covid = True  # COVID marked with a labelled line (a heat map can't be shaded)
    cb = fig.colorbar(im, cax=cax, extend="both")
    cb.set_label("Demand above the 2016–19 weather model, MW", color=t.muted)
    cb.outline.set_visible(False)
    return fig, (
        "Two heat maps, week by hour of day. Dominion turns steadily warmer across all "
        "24 hours from 2022; the comparison region stays near zero."
    )


# --------------------------------------------------------------------------- chart 4


def chart4(t: Theme):
    from glg.models import supply_gap as sg

    since = pl.read_csv(sg.SINCE_PATH)
    dc = ["DOM", "ERCO", "AZPS+SRP", "BPAT", "NEVP", "GCPD"]
    comp = [
        "PJM",
        "SWPP",
        "MISO",
        "TVA",
        "FPL+SOCO",
        "DUK",
        "CPLE",
        "FPC",
        "PACE",
        "CISO",
        "ISNE",
        "NYIS",
    ]
    d = {r["series"]: r for r in since.iter_rows(named=True)}
    rows = (
        [("Data-center regions", None)]
        + [(b, d[b]) for b in sorted(dc, key=lambda b: d[b]["gap_pct"])]
        + [("Other regions", None)]
        + [(b, d[b]) for b in sorted(comp, key=lambda b: d[b]["gap_pct"])]
    )
    fig, ax = plt.subplots(figsize=(10, 8.6))
    y = 0
    for b, r in rows:
        if r is None:
            ax.text(-34.5, y, b, fontsize=10, fontweight="bold", color=t.ink, va="center")
            y += 1
            continue
        gap = r["gap_pct"]
        ax.barh(y, gap, color=t.contrast if gap < 0 else t.focus, height=0.66, zorder=3)
        ax.text(-34.5, y, NAMES[b], fontsize=9, color=t.ink, va="center")
        x = gap - 0.6 if gap < 0 else gap + 0.6
        ax.text(
            x,
            y,
            "0%" if abs(gap) < 0.5 else f"{gap:+.0f}%",
            fontsize=8.5,
            color=t.ink,
            va="center",
            ha="right" if gap < 0 else "left",
            fontweight="bold",
        )
        ax.text(
            14,
            y,
            f"demand {r['demand_twh_added']:+.1f} TWh, new supply {r['new_supply_twh']:+.1f} TWh",
            fontsize=8,
            color=t.muted,
            va="center",
        )
        y += 1
    ax.axvline(0, color=t.ink, lw=0.8, zorder=4)
    ax.set_xlim(-35, 30)
    ax.set_ylim(y - 0.4, -0.6)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.set_xticks([-25, -20, -15, -10, -5, 0, 5, 10])
    style_axes(ax, t, grid="x")
    ax.set_xlabel("New local generation minus demand growth, % of start-year demand")
    ax.text(
        -0.8,
        -0.05,
        "◀ demand outgrew local supply",
        fontsize=8.5,
        color=t.ink,
        ha="right",
        va="center",
    )
    ax.text(
        0.8,
        -0.05,
        "local supply outgrew demand ▶",
        fontsize=8.5,
        color=t.ink,
        ha="left",
        va="center",
    )
    rect = frame(
        fig,
        t,
        "Dominion's demand grew by a quarter since 2022; local generation barely moved",
        "Weather-adjusted demand growth vs expected output of generators added minus retired, "
        "2022 to the 12 months to Oct 2026",
        "Chart 4 · Sources: EIA-930 and ERA5 (demand, weather-adjusted with a 2016–19 GAM); "
        "EIA-860M Sept 2026 (generators); EIA Electric Power Monthly 6.07.A/B\n(capacity "
        "factors, 2023–25). Batteries count as zero energy. Phoenix to 2025-08. Regions trade "
        "power: a deficit is met by imports or by running existing plants harder.",
    )
    fig.tight_layout(rect=rect)
    r = d["DOM"]
    return fig, (
        f"Diverging bars. Dominion: demand {r['demand_twh_added']:+.1f} TWh a year vs "
        f"new local supply {r['new_supply_twh']:+.1f} TWh, a gap of {r['gap_pct']:.0f}% "
        "of 2022 demand."
    )


# --------------------------------------------------------------------------- chart 5


def chart5(t: Theme):
    from glg.models import queue_summary as qs

    active = pl.read_csv(qs.ACTIVE_PATH)
    by_year = pl.read_csv(qs.ACTIVE_YEAR_PATH)
    # Nuclear (<= 3.7 GW in any region) folds into Other; stack order keeps amber and
    # vermillion apart (dark-theme CVD, see theme.py).
    types = ["Solar", "Hybrid", "Wind", "Battery", "Gas", "Other"]
    colors = [t.amber, t.magenta, t.sky, t.green, t.vermillion, t.other]
    label = {
        "Hybrid": "Hybrid (solar/wind + storage)",
        "Battery": "Storage",
        "Other": "Other (incl. nuclear)",
    }
    regions = (
        active.filter(pl.col("level") == "region")
        .sort("total_gw", descending=True)["unit"]
        .to_list()
    )
    names = {"West": "West (non-ISO)", "Southeast": "Southeast (non-ISO)"}
    yr = by_year.filter(pl.col("level") == "region").with_columns(
        pl.col("q_year").clip(2015, 2025).alias("yr"),
        pl.when(pl.col("type_group") == "Nuclear")
        .then(pl.lit("Other"))
        .otherwise(pl.col("type_group"))
        .alias("type_group"),
    )
    years = list(range(2015, 2026))
    fig, axes = plt.subplots(3, 3, figsize=(11.5, 9.4), sharey=True, sharex=True)
    for ax, reg in zip(axes.flat, regions, strict=False):
        d = yr.filter(pl.col("unit") == reg).group_by("yr", "type_group").agg(pl.col("gw").sum())
        bottom = np.zeros(len(years))
        for ty, col in zip(types, colors, strict=False):
            v = np.array(
                [
                    d.filter((pl.col("yr") == y) & (pl.col("type_group") == ty))["gw"].sum()
                    for y in years
                ]
            )
            ax.bar(
                years,
                v,
                bottom=bottom,
                color=col,
                width=0.8,
                edgecolor=t.bg,
                linewidth=0.7,
                zorder=3,
            )
            bottom += v
        tot = active.filter((pl.col("level") == "region") & (pl.col("unit") == reg))["total_gw"][0]
        ax.set_title(f"{names.get(reg, reg)}   {tot:,.0f} GW", fontsize=10)
        style_axes(ax, t)
        ax.set_xticks([2015, 2018, 2021, 2024], ["≤2015", "2018", "2021", "2024"])
    for ax in axes[:, 0]:
        ax.set_ylabel("Active requests, GW")
    axes[0, 0].set_ylim(0, None)
    total = active.filter(pl.col("level") == "region")["total_gw"].sum()
    rect = frame(
        fig,
        t,
        f"{total / 1000:.1f} TW of power plants wait in U.S. interconnection queues, mostly "
        "solar and storage",
        "Active requests at the end of 2025 by queue-entry year and technology. Of requests "
        "entered in 2000–2020, 19% reached operation (13% of MW).",
        "Chart 5 · Source: Lawrence Berkeley National Laboratory and GridTracker, Queued Up: 2026 "
        "Edition (data through 2025), CC BY 4.0. Hybrids count all components; suspended "
        "requests excluded.",
        legend=True,
    )
    legend_row(
        fig,
        t,
        [Patch(color=c, label=label.get(ty, ty)) for ty, c in zip(types, colors, strict=False)],
        fontsize=8.8,
    )
    fig.tight_layout(rect=rect)
    return fig, (
        f"Nine stacked bar charts of active interconnection requests by entry year; "
        f"{total / 1000:.1f} TW in total, mostly solar, hybrid and storage."
    )


# --------------------------------------------------------------------------- chart 6


def chart6(t: Theme):
    from glg.ingest import iso_markets as im

    c = pl.read_parquet(im.CAPACITY_PATH).sort("start_year")
    x = c["start_year"].to_numpy()
    y = c["rto_price"].to_numpy()
    fig, ax = plt.subplots(figsize=(11, 6.8))
    ax.step(
        np.append(x, x[-1] + 1), np.append(y, y[-1]), where="post", color=t.focus, lw=2.2, zorder=3
    )
    d = c.filter(pl.col("dom_in_rpm"))
    dx, dy = d["start_year"].to_numpy(), d["dom_price"].to_numpy()
    ax.step(
        np.append(dx, dx[-1] + 1),
        np.append(dy, dy[-1]),
        where="post",
        color=t.contrast,
        lw=2.2,
        ls=(0, (4, 2)),
        zorder=4,
    )
    u = c.filter(pl.col("rto_uncapped").is_not_null())
    ax.scatter(
        u["start_year"] + 0.5,
        u["rto_uncapped"],
        s=46,
        facecolor=t.bg,
        edgecolor=t.focus,
        lw=1.6,
        zorder=5,
    )
    for r in u.iter_rows(named=True):
        ax.annotate(
            f"${r['rto_uncapped']:.0f}",
            (r["start_year"] + 0.5, r["rto_uncapped"]),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color=t.ink,
        )
    ax.fill_between([2026, 2029], 175, 325, color=t.covid, zorder=1)
    ax.text(
        2028.95,
        182,
        "price floor–cap band\n(2026/27 onward)",
        fontsize=7.5,
        color=t.muted,
        ha="right",
        va="bottom",
    )
    for r in c.iter_rows(named=True):
        if r["start_year"] in (2018, 2021, 2022, 2023, 2024, 2025, 2026, 2027, 2028):
            ax.annotate(
                f"${r['rto_price']:.0f}",
                (r["start_year"] + 0.5, r["rto_price"]),
                xytext=(0, -13 if r["start_year"] < 2025 else 10),
                textcoords="offset points",
                ha="center",
                fontsize=8.5,
                color=t.ink,
                fontweight="bold",
            )
    arrow = dict(arrowstyle="-", color=t.muted, lw=0.8)
    ax.annotate(
        "DOM $444: Dominion joins the auction\nand clears as a constrained zone",
        (2025.5, 444.26),
        xytext=(2022.7, 520),
        fontsize=8.5,
        color=t.ink,
        arrowprops=arrow,
    )
    ax.annotate(
        "Price jumps ~9x\nas reserve margins tighten",
        (2025.05, 269.92),
        xytext=(2021.6, 300),
        fontsize=8.5,
        color=t.ink,
        arrowprops=arrow,
    )
    ax.annotate(
        "Clears at the cap, 6.5 GW (2027/28) and 6.8 GW (2028/29)\nshort of the "
        'reliability requirement; PJM cites\nforecast growth "largely due to additional '
        'Large Loads"',
        (2027.5, 333),
        xytext=(2019.3, 395),
        fontsize=8.5,
        color=t.ink,
        arrowprops=arrow,
    )
    ax.text(2023.55, 72, "Three years under \\$55", fontsize=8.5, color=t.muted, ha="center")
    ax.set_xlim(2018, 2029)
    ax.set_ylim(0, 600)
    ax.set_xticks(range(2018, 2029), [f"{yy}/{str(yy + 1)[2:]}" for yy in range(2018, 2029)])
    ax.set_ylabel("$ per MW-day (UCAP)")
    ax.set_xlabel("Delivery year (June to May)")
    style_axes(ax, t)
    rect = frame(
        fig,
        t,
        "PJM capacity prices went from record lows to the price cap in three auctions",
        "Base Residual Auction clearing price by delivery year. Descriptive: prices also reflect "
        "retirements, rule changes and gas prices.",
        "Chart 6 · Source: PJM Base Residual Auction reports, 2025/26 to 2028/29 (RTO history: "
        "2028/29 report, Table 2). Cap \\$325–333/MW-day UCAP, floor \\$175–180.\nDominion's "
        "zone was outside the auction (Fixed Resource Requirement) before 2025/26.",
        legend=True,
    )
    legend_row(
        fig,
        t,
        [
            Line2D([], [], color=t.focus, lw=2.2, label="RTO clearing price"),
            Line2D(
                [],
                [],
                color=t.contrast,
                lw=2.2,
                ls=(0, (4, 2)),
                label="Dominion (DOM) zone, in the auction from 2025/26",
            ),
            Line2D(
                [],
                [],
                marker="o",
                ls="",
                markerfacecolor=t.bg,
                markeredgecolor=t.focus,
                label="PJM's simulated price with no cap",
            ),
        ],
    )
    fig.tight_layout(rect=rect)
    return fig, (
        "Step chart of PJM capacity prices: under $55/MW-day for three years, $270 in "
        "2025/26 (Dominion zone $444), then at the cap of about $330."
    )


# --------------------------------------------------------------------------- chart 7


def chart7(t: Theme):
    bas = ["DOM", "ERCO", "SOCO", "AZPS"]
    flags = pl.read_csv(OUTPUTS_DIR / "validation_flags.csv")
    metrics = pl.read_csv(
        OUTPUTS_DIR / "validation_metrics.csv", schema_overrides={"holdout": pl.Utf8}
    )
    pred = pl.read_parquet(OUTPUTS_DIR / "baseline_predictions.parquet").filter(
        pl.col("model") == "gam"
    )
    fig, ax = plt.subplots(2, 4, figsize=(15, 7.4), gridspec_kw={"height_ratios": [1.25, 1]})
    for j, ba in enumerate(bas):
        d = pred.filter(pl.col("ba") == ba)
        wk = d.filter(
            pl.col("period_end_utc").is_between(
                pl.datetime(2019, 7, 15, time_zone="UTC"), pl.datetime(2019, 7, 22, time_zone="UTC")
            )
        )
        a = ax[0, j]
        a.plot(wk["period_end_utc"], wk["actual"] / 1e3, color=t.ink, lw=1.6)
        a.plot(wk["period_end_utc"], wk["pred_oof"] / 1e3, color=t.focus, lw=1.8, ls="--")
        m = metrics.filter(
            pl.col("ba") == ba, pl.col("model") == "gam", pl.col("holdout") == "2019"
        ).row(0, named=True)
        held = "July 2019 held out" if ba == "DOM" else "2019 held out"
        a.set_title(
            f"{NAMES.get(ba, ba)} · {held}\nerror {m['mape_pct']:.1f}% (MAPE), "
            f"bias {m['bias_pct']:+.1f}%",
            fontsize=9.5,
        )
        a.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        a.xaxis.set_major_locator(mdates.DayLocator(interval=2))
        a.set_ylabel("Demand, GW" if j == 0 else "")
        mo = (
            d.group_by(pl.col("period_end_utc").dt.truncate("1mo").alias("m"))
            .agg(
                (
                    100
                    * (pl.col("actual") - pl.col("pred_insample")).sum()
                    / pl.col("actual").sum()
                ).alias("r"),
                pl.len().alias("n"),
            )
            .filter(pl.col("n") >= 480)
            .sort("m")
        )
        tt = (mo["m"].dt.year() + (mo["m"].dt.month() - 0.5) / 12).to_numpy()
        b = ax[1, j]
        b.axhline(0, color=t.rule, lw=1)
        b.plot(mo["m"], mo["r"], color=t.ink, lw=1.3, marker="o", ms=3)
        k = np.polyfit(tt, mo["r"].to_numpy(), 1)
        tr = flags.filter(pl.col("ba") == ba, pl.col("model") == "gam")["trend_pct_per_yr"][0]
        b.plot(mo["m"], np.polyval(k, tt), color=t.contrast, lw=1.8)
        b.text(
            0.02,
            0.92,
            f"trend {tr:+.1f}%/yr",
            transform=b.transAxes,
            fontsize=8.5,
            color=t.ink,
            va="top",
        )
        b.set_ylim(-6, 6)
        b.set_xlim(np.datetime64("2015-12-01"), np.datetime64("2020-01-31"))
        _years_axis(b, 1)
        b.set_ylabel("Actual − model, % of demand" if j == 0 else "")
        for x in ax[:, j]:
            style_axes(x, t)
    rect = frame(
        fig,
        t,
        "The baseline model tracks weather it never saw; what it leaves is each region's own pre-2022 trend",
        "Top: a July 2019 week the model was not fitted on. Bottom: monthly residual over the "
        "baseline, with its linear trend (what growth since 2022 is also measured against).",
        "Chart 7 · Source: EIA-930 hourly demand (DOM: EIA-930 PJM subregion), ERA5 temperature. "
        "GAM fitted per region, leave-one-year-out (DOM: leave-one-month-out, data from 2018-09).",
        legend=True,
    )
    legend_row(
        fig,
        t,
        [
            Line2D([], [], color=t.ink, lw=1.6, label="Actual demand"),
            Line2D([], [], color=t.focus, lw=1.8, ls="--", label="Model, period held out"),
            Line2D([], [], color=t.ink, lw=1.3, marker="o", ms=3, label="Monthly residual"),
            Line2D([], [], color=t.contrast, lw=1.8, label="Residual trend, 2016–19"),
        ],
    )
    fig.tight_layout(rect=rect)
    fig._glg_covid = True  # pre-COVID window only: nothing to shade
    return fig, (
        "Four regions: model vs actual over a held-out July week, and baseline "
        "residuals within ±6% with small trends."
    )


# --------------------------------------------------------------------------- chart 8


def chart8(t: Theme):
    """Our DOM estimate vs Dominion's metered data-center demand, change since 2022."""
    _, rank, _, annual = _growth()
    dc = pl.read_csv(REFERENCE_DIR / "dominion_dc_demand.csv")
    ours = annual.filter(pl.col("ba") == "DOM", pl.col("year").is_between(2019, 2026)).sort("year")
    base = ours.filter(pl.col("year") == 2022)["resid_mw"][0]
    fig, ax = plt.subplots(figsize=(10, 6.4))
    ax.axhline(0, color=t.rule, lw=0.9, zorder=1)
    shade_covid(ax, t, years=True)
    ends = {}

    def line(xs, ys, partial, color, ls, label, key):
        xs, ys = list(xs), list(ys)
        solid = [i for i, p in enumerate(partial) if not p]
        ax.plot(
            [xs[i] for i in solid],
            [ys[i] for i in solid],
            color=color,
            lw=2.2,
            ls=ls,
            marker="o",
            ms=6,
            zorder=3,
        )
        if partial[-1]:
            ax.plot(xs[-2:], ys[-2:], color=color, lw=1.4, ls=":", zorder=3)
            ax.scatter(xs[-1], ys[-1], s=40, facecolor=t.bg, edgecolor=color, lw=1.6, zorder=4)
        ends[key] = (xs[-1], ys[-1], label)

    line(
        ours["year"],
        ours["resid_mw"] - base,
        ours["partial_year"].to_list(),
        t.focus,
        "-",
        "Our estimate: weather-adjusted\ngrowth, PJM DOM zone (avg MW)",
        "ours",
    )
    for col, ls, lab in [
        ("coincident_mw", "-", "Dominion: data-center demand at\nsystem peak (coincident)"),
        ("billing_mw", "--", "Dominion: data-center\nbilling demand"),
    ]:
        v = dc.filter(pl.col(col).is_not_null(), pl.col("year").is_between(2019, 2026)).sort("year")
        b0 = v.filter(pl.col("year") == 2022)[col][0]
        line(v["year"], v[col] - b0, v["forecast"].to_list(), t.contrast, ls, lab, col)
    pos = _spread({k: e[1] for k, e in ends.items()}, 420)
    for k, (x, _, lab) in ends.items():
        ax.text(x + 0.18, pos[k], lab, fontsize=8.5, color=t.ink, va="center", linespacing=1.15)
    ax.set_xlim(2018.7, 2028.6)
    ax.set_xticks(range(2019, 2027))
    ax.set_ylabel("Change since 2022, MW")
    style_axes(ax, t)
    d = {r["year"]: r for r in dc.iter_rows(named=True)}
    o = {r["year"]: r["resid_mw"] for r in ours.iter_rows(named=True)}
    share = (d[2025]["coincident_mw"] - d[2022]["coincident_mw"]) / (o[2025] - o[2022])
    rect = frame(
        fig,
        t,
        "Dominion's metered data-center demand rises in step with our estimate, but explains part of it",
        f"Change since 2022. Metered data-center demand accounts for about {100 * share:.0f}% "
        "of our 2022–2025 estimate; the rest is consistent with other load in the wider zone.",
        "Chart 8 · Sources: our estimate from EIA-930 (DOM: PJM subregion) and ERA5, 2016–19 GAM; "
        "annual average MW, 2026 = Jan–Oct (open marker). Dominion Energy, 'Historical\nData "
        "Center Demand', PJM Load Analysis Subcommittee, 2026-09-29 (2026: forecast, open "
        "marker). Dominion's territory is smaller than PJM's DOM zone (co-ops serve the rest);\n"
        "its MW are peak measures, ours an annual average. Shaded: COVID.",
    )
    fig.tight_layout(rect=rect)
    return fig, (
        f"Line chart: Dominion's metered data-center demand rose about "
        f"{d[2025]['coincident_mw'] - d[2022]['coincident_mw']:,.0f} MW from 2022 to 2025; "
        f"our weather-adjusted estimate for the zone rose {o[2025] - o[2022]:,.0f} MW."
    )


# --------------------------------------------------------------------------- context


def retail(t: Theme):
    from glg.ingest import eia861

    r = pl.read_parquet(eia861.RETAIL_PATH)
    col = {"VA": t.vermillion, "GA": t.green, "TX": t.blue, "AZ": t.magenta, "US": t.ink}
    name = {"VA": "Virginia", "TX": "Texas", "GA": "Georgia", "AZ": "Arizona", "US": "U.S. average"}
    real = [c for c in r.columns if c.startswith("price_real_")][0]
    fig, axes = plt.subplots(1, 3, figsize=(12, 5.4), sharey=True)
    for ax, sec in zip(axes, ["RES", "COM", "IND"], strict=False):
        ends = {}
        for st in ["US", "VA", "TX", "GA", "AZ"]:
            d = r.filter((pl.col("state") == st) & (pl.col("sector") == sec)).sort(
                "year", "trailing"
            )
            a = d.filter(~pl.col("trailing"))
            ax.plot(
                a["year"],
                a[real],
                color=col[st],
                lw=2.4 if st == "US" else 1.8,
                ls="--" if st == "US" else "-",
                zorder=3,
            )
            tr = d.filter(pl.col("trailing"))
            if tr.height:
                ax.plot(
                    [a["year"][-1], tr["year"][0] - 0.33],
                    [a[real][-1], tr[real][0]],
                    color=col[st],
                    lw=1.2,
                    ls=":",
                    zorder=3,
                )
                ax.scatter([tr["year"][0] - 0.33], tr[real], color=col[st], s=18, zorder=4)
                ends[st] = tr[real][0]
            else:
                ends[st] = a[real][-1]
        for k, yv in _spread(ends, 0.8).items():
            ax.text(
                2026.15, yv, name[k] if sec == "IND" else k, fontsize=8, color=t.ink, va="center"
            )
        shade_covid(ax, t, years=True)
        ax.set_title({"RES": "Residential", "COM": "Commercial", "IND": "Industrial"}[sec])
        style_axes(ax, t)
        ax.set_xlim(2010, 2027.6)
        ax.set_ylim(0, None)
    axes[0].set_ylabel("Cents per kWh, 2025 dollars")
    rect = frame(
        fig,
        t,
        "Retail electricity prices in data-center states vs the U.S. average",
        "Average revenue per kWh, inflation-adjusted. Dot: 12 months to the latest month. "
        "Shaded: COVID. Descriptive only.",
        "Source: EIA electricity retail sales (EIA-861/861M) via EIA API v2; CPI-U (CPIAUCSL) via "
        "FRED. U.S. average dashed.",
    )
    fig.tight_layout(rect=rect)
    return fig, (
        "Three line charts of real retail prices, 2010 to 2026: Virginia rises faster "
        "than the U.S. average since 2021; Texas, Georgia and Arizona are flat or down."
    )


def robustness(t: Theme):
    from glg.models import growth as g
    from glg.models import robustness as rb

    grid = pl.read_csv(rb.GRID_PATH)
    rank = pl.read_csv(g.RANKING_PATH)
    spec = {
        "gbm": ("s", "LightGBM"),
        "ddr": ("^", "Degree-day OLS"),
        "gam_2017_19": ("v", "Baseline 2017–19"),
        "gam_start_yr": ("P", "Fitted on 2022 itself"),
        "gam_emp": ("X", "With employment control"),
        "gam_phx_warm": ("*", "Phoenix ERA5 +0.3 °C"),
    }
    mid = ["PJM", "SWPP", "TVA", "SOCO", "FPL+SOCO"]
    order = [b for b in rb.DC + mid + rb.COMP if b in grid["ba"].to_list()]
    gd = {r["ba"]: r for r in grid.iter_rows(named=True)}
    rk = {r["ba"]: r for r in rank.iter_rows(named=True)}
    fig, ax = plt.subplots(figsize=(10, 9.6))
    ys = []
    for i, b in enumerate(order):
        y = -i - (1.0 if b in rb.COMP or b in mid else 0) - (1.0 if b in rb.COMP else 0)
        ys.append(y)
        r = gd[b]
        ax.plot(
            [r["min_pct"], r["max_pct"]],
            [y, y],
            color=t.faint,
            lw=7,
            zorder=1,
            solid_capstyle="round",
        )
        for s, (mk, _) in spec.items():
            if s == "gam_phx_warm" and b != "AZPS+SRP":
                continue
            ax.scatter(r[s], y, marker=mk, s=28, color=t.muted, zorder=2)
        k = rk[b]
        ax.errorbar(
            k["growth_pct"],
            y,
            xerr=[[k["growth_pct"] - k["growth_lo"]], [k["growth_hi"] - k["growth_pct"]]],
            fmt="o",
            color=t.focus,
            ms=7,
            capsize=2,
            lw=1.5,
            zorder=3,
        )
        ax.scatter(
            k["beyond_trend_pct"], y, marker="D", s=30, facecolor=t.bg, edgecolor=t.focus, zorder=3
        )
        ax.text(-9.5, y, NAMES.get(b, b), va="center", fontsize=9, color=t.ink)
    ax.axvline(0, color=t.ink, lw=0.8)
    for lab, yy in [
        ("Data-center regions", 0.9),
        ("Other PJM / Southeast / SPP", ys[len(rb.DC)] + 0.9),
        ("Comparison regions", ys[-len(rb.COMP)] + 0.9),
    ]:
        ax.text(-9.5, yy, lab, fontsize=9.5, fontweight="bold", color=t.ink)
    ax.set_yticks([])
    ax.set_xlim(-10, 36)
    ax.spines["left"].set_visible(False)
    style_axes(ax, t, grid="x")
    ax.set_xlabel("Weather-adjusted demand growth since 2022, % (FPL, Southern: since 2023)")
    h = [
        Line2D(
            [], [], marker="o", color=t.focus, ls="", label="Headline (GAM 2016–19), 95% interval"
        ),
        Line2D(
            [],
            [],
            marker="D",
            markerfacecolor=t.bg,
            markeredgecolor=t.focus,
            ls="",
            label="Beyond own 2016–19 trend",
        ),
    ]
    h += [Line2D([], [], marker=mk, color=t.muted, ls="", label=lab) for mk, lab in spec.values()]
    ax.legend(handles=h, loc="lower right", fontsize=8.5)
    rect = frame(
        fig,
        t,
        "Dominion leads under every specification; the size of the gap depends on the grouping",
        "Weather-adjusted growth since 2022 across model families, baselines, the employment "
        "control and the Phoenix weather check. Gray bar: lowest to highest.",
        "Source: EIA-930 hourly demand, ERA5 weather; this analysis (data/outputs/"
        "robustness_grid.csv). Phoenix to 2025-08. Data-center regions lead comparison regions "
        "by 8–10 points with this grouping,\n2–6 with the groups written down before any "
        "weather-adjusted result. ERCOT: 11–20% across specifications; the large loads it tracks "
        "(data centers, crypto, industry) peak at about 41% of its growth.",
    )
    fig.tight_layout(rect=rect)
    r = gd["DOM"]
    return fig, (
        f"Forest plot of growth since 2022 by region and specification. Dominion ranges "
        f"{r['min_pct']:.0f}–{r['max_pct']:.0f}%, highest in every specification."
    )


# --------------------------------------------------------------------------- registry


@dataclass(frozen=True)
class Spec:
    stem: str
    build: Callable[[Theme], tuple]
    time_series: bool  # needs COVID shading
    social: bool = False


FIGURES = [
    Spec("hero", hero, True),
    Spec("chart2_rising_floor", chart2, True),
    Spec("chart3_dom_heat_calendar", chart3, True),
    Spec("chart4_supply_gap", chart4, False),
    Spec("chart5_queue_backlog", chart5, False),
    Spec("chart6_pjm_capacity", chart6, False),
    Spec("chart7_validation", chart7, True),
    Spec("chart8_dominion_metered", chart8, True),
    Spec("retail_prices", retail, True),
    Spec("robustness_forest", robustness, False),
    Spec("hero_card", card_hero, True, social=True),
    Spec("chart2_card", card_chart2, True, social=True),
]


def build(spec: Spec, t: Theme) -> list:
    with use(t):
        fig, alt = spec.build(t)
        if spec.time_series and not getattr(fig, "_glg_covid", False):
            raise AssertionError(f"{spec.stem}: time-series chart without COVID shading")
        if not getattr(fig, "_glg_title", None):
            raise AssertionError(f"{spec.stem}: no title")
        return save(fig, t, spec.stem, SOCIAL_DIR if spec.social else FIGURES_DIR, alt)


def main(stems: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for spec in FIGURES:
        if stems and spec.stem not in stems:
            continue
        for t in THEMES.values():
            paths = build(spec, t)
            log.info("%s (%s): %s", spec.stem, t.name, ", ".join(p.name for p in paths))


if __name__ == "__main__":
    main(sys.argv[1:] or None)
