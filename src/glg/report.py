# ruff: noqa: E501
"""Numbers, tables and figure markup for the Quarto report (Prompt 17).

`report/index.qmd` never types a result by hand: every number in its prose comes from
`numbers()` and every results table from a function here, read from `data/outputs/`. If
the pipeline moves a number, the report moves with it, and `tests/test_report.py` checks
the key values against the same pins as `tests/test_pinned_estimates.py`.

Nothing here reads `data/outputs/private/` (PJM Data Miner, decision D3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
from statistics import median

import polars as pl

from glg.config import FIGURES_DIR, OUTPUTS_DIR, REFERENCE_DIR

DC = ["DOM", "ERCO", "AZPS+SRP", "NEVP", "GCPD", "BPAT"]
COMPARISONS = ["MISO", "NYIS", "ISNE", "FPC", "CPLE", "CISO", "PACE", "FPL"]

NAMES = {
    "DOM": "Dominion zone (Virginia, PJM)",
    "ERCO": "ERCOT (Texas)",
    "AZPS+SRP": "Phoenix (APS + SRP)",
    "AZPS": "Arizona Public Service",
    "SRP": "Salt River Project",
    "BPAT": "Bonneville (Pacific Northwest)",
    "SWPP": "Southwest Power Pool",
    "GCPD": "Grant County PUD (Washington)",
    "NEVP": "NV Energy (Nevada)",
    "PACE": "PacifiCorp East",
    "PJM": "PJM (all, 13 states + DC)",
    "CISO": "California ISO",
    "TVA": "Tennessee Valley Authority",
    "SOCO": "Southern Company (Georgia, Alabama)",
    "MISO": "MISO (Midwest)",
    "FPL+SOCO": "FPL + Southern combined",
    "FPL": "Florida Power & Light",
    "DUK": "Duke Energy Carolinas",
    "FPC": "Duke Energy Florida",
    "CPLE": "Duke Energy Progress East",
    "ISNE": "ISO New England",
    "NYIS": "New York ISO",
}

ROLE = {**{b: "data-center" for b in DC}, **{b: "comparison" for b in COMPARISONS}}
OTHER = "neither ³"


def _csv(name: str) -> pl.DataFrame:
    return pl.read_csv(OUTPUTS_DIR / f"{name}.csv")


def _rows(df: pl.DataFrame, key: str = "ba") -> dict[str, dict]:
    return {r[key]: r for r in df.iter_rows(named=True)}


def pct(x: float, nd: int = 1, sign: bool = True) -> str:
    s = f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"
    return s.replace("-", "−") + "%"


def num(x: float, nd: int = 0, sign: bool = False) -> str:
    s = f"{x:+,.{nd}f}" if sign else f"{x:,.{nd}f}"
    return s.replace("-", "−")


def gw(mw: float, nd: int = 1, sign: bool = True) -> str:
    return num(mw / 1000, nd, sign) + " GW"


def rng(lo: float, hi: float, nd: int = 0) -> str:
    return f"{lo:.{nd}f} to {hi:.{nd}f}" if lo < 0 else f"{lo:.{nd}f}–{hi:.{nd}f}"


def ci(lo: float, hi: float) -> str:
    return rng(lo, hi, 1).replace("-", "−")


@dataclass(frozen=True)
class Numbers:
    """Attribute access for inline `{python} N.x` in the report."""

    d: dict

    def __getattr__(self, k: str) -> str:
        try:
            return self.d[k]
        except KeyError as e:  # pragma: no cover - a typo in the .qmd should fail loudly
            raise AttributeError(k) from e


def numbers() -> Numbers:
    rank = _rows(_csv("growth_ranking"))
    dec = _rows(_csv("growth_decomposition"))
    grid = _rows(_csv("robustness_grid"))
    med = _csv("robustness_medians")
    sup = _rows(_csv("supply_vs_demand_since2022"), "series")
    sup_r = _rows(_csv("supply_vs_demand_since2022_regional_cf"), "series")
    pairs = _rows(_csv("comparison_pairs"), "dc_region")
    qp = _rows(_csv("queue_pipeline"), "series")
    ann = pl.read_csv(OUTPUTS_DIR / "structural_growth_annual.csv")
    dcd = pl.read_csv(REFERENCE_DIR / "dominion_dc_demand.csv")
    val = _csv("validation_flags").filter(pl.col("model") == "gam")
    bra = pl.read_csv(REFERENCE_DIR / "pjm_bra_prices.csv")

    d: dict[str, str] = {}
    for b, r in rank.items():
        k = b.lower().replace("+", "_")
        d[f"{k}_g"] = pct(r["growth_pct"])
        d[f"{k}_ci"] = ci(r["growth_lo"], r["growth_hi"]) + "%"
        d[f"{k}_mw"] = num(r["mw_added"], 0, True) + " MW"
        d[f"{k}_gw"] = gw(r["mw_added"])
        d[f"{k}_twh"] = num(r["twh_per_yr_added"], 1, True) + " TWh"
        d[f"{k}_beyond"] = pct(r["beyond_trend_pct"])
        d[f"{k}_trend"] = num(r["baseline_trend_pct_yr"], 1, True) + "%/yr"
    for b in ["DOM", "ERCO", "AZPS+SRP", "BPAT", "GCPD", "NEVP", "PJM"]:
        k = b.lower().replace("+", "_")
        r = grid[b]
        d[f"{k}_spec_range"] = f"{r['min_pct']:.1f}–{r['max_pct']:.1f}%"
        d[f"{k}_emp"] = pct(r["gam_emp"])
    d["phx_warm"] = pct(grid["AZPS+SRP"]["gam_phx_warm"])

    # data-center vs comparison medians
    m = med.filter(~pl.col("excl_boundary_bas"))
    head = m.filter((pl.col("grouping") == "dc6_vs_d10") & (pl.col("spec") == "gam_2016_19")).row(
        0, named=True
    )
    d["dc_med"] = pct(head["dc_median_pct"])
    d["comp_med"] = pct(head["comp_median_pct"])
    d["gap_head"] = f"{head['gap_pts']:.0f}"
    dc6 = med.filter(pl.col("grouping") == "dc6_vs_d10")
    pre = med.filter(pl.col("grouping") == "prompt2_roles")
    d["gap_dc6"] = rng(dc6["gap_pts"].min(), dc6["gap_pts"].max())
    d["gap_pre"] = rng(pre["gap_pts"].min(), pre["gap_pts"].max())
    d["gap_all"] = rng(med["gap_pts"].min(), med["gap_pts"].max())
    d["ratio_all"] = rng(med["ratio"].min(), med["ratio"].max(), 1) + "×"
    d["n_specs"] = str(m["spec"].n_unique())

    # load shape
    for b in ["DOM", "ERCO", "AZPS+SRP", "BPAT", "CPLE", "CISO", "SOCO"]:
        k = b.lower().replace("+", "_")
        r = dec[b]
        d[f"{k}_night_mw"] = gw(r["d_night_mw"], 2)
        d[f"{k}_aft_mw"] = gw(r["d_afternoon_mw"], 2)
        d[f"{k}_night_g"] = pct(r["night_growth_pct"])
        d[f"{k}_aft_g"] = pct(r["afternoon_growth_pct"])
        d[f"{k}_n2a"] = f"{r['night_to_afternoon']:.2f}"
        d[f"{k}_weather_mw"] = num(r["d_weather_mw"], 0, True) + " MW"
    p = pairs["DOM"]
    d["pooled_night"] = pct(p["pooled_night_growth_pct"])
    d["dc_night_med"] = pct(median(dec[b]["night_growth_pct"] for b in DC))

    # supply
    s = sup["DOM"]
    d["dom_dem_twh"] = num(s["demand_twh_added"], 1, True) + " TWh/yr"
    d["dom_sup_twh"] = num(s["new_supply_twh"], 1, True) + " TWh/yr"
    d["dom_sup_share"] = f"{100 * s['supply_to_demand']:.0f}%"
    d["dom_sup_share_reg"] = f"{100 * sup_r['DOM']['supply_to_demand']:.0f}%"
    d["dom_gap_pct"] = f"{-s['gap_pct']:.0f}%"
    d["erco_sup_twh"] = num(sup["ERCO"]["new_supply_twh"], 1, True) + " TWh/yr"
    d["erco_dem_twh"] = num(sup["ERCO"]["demand_twh_added"], 1, True) + " TWh/yr"
    d["erco_storage_gw"] = gw(sup["ERCO"]["new_storage_mw"])
    d["pjm_sup_twh"] = num(sup["PJM"]["new_supply_twh"], 1, True) + " TWh/yr"
    d["pjm_dem_twh"] = num(sup["PJM"]["demand_twh_added"], 1, True) + " TWh/yr"
    d["dom_queue_gw"] = f"{qp['DOM']['active_gw']:.0f} GW"
    d["dom_queue_exp"] = f"{qp['DOM']['expected_twh']:.1f} TWh/yr"
    d["dom_queue_compl"] = f"{100 * qp['DOM']['completion']:.0f}%"

    # Dominion metered data-center demand vs our DOM residual
    res = dict(ann.filter(pl.col("ba") == "DOM").select("year", "resid_mw").iter_rows())
    co = dict(dcd.select("year", "coincident_mw").drop_nulls().iter_rows())
    bi = dict(dcd.select("year", "billing_mw").drop_nulls().iter_rows())
    d_co, d_res = co[2025] - co[2022], res[2025] - res[2022]
    d["dc_metered_22_25"] = num(d_co, 0, True) + " MW"
    d["dom_resid_22_25"] = num(d_res, 0, True) + " MW"
    d["dc_share_22_25"] = f"{100 * d_co / d_res:.0f}%"
    d["dc_billing_19_25"] = num(bi[2025] - bi[2019], 0, True) + " MW"
    d["dom_resid_19_25"] = num(res[2025] - res[2019], 0, True) + " MW"
    d["dc_share_19_25"] = f"{100 * (bi[2025] - bi[2019]) / (res[2025] - res[2019]):.0f}%"
    gres = dict(ann.filter(pl.col("ba") == "GCPD").select("year", "resid_mw").iter_rows())
    d["gcpd_resid_2025"] = num(gres[2025], 0, True) + " MW"
    d["dc_peak_2025"] = gw(co[2025], 1, False)
    d["dc_billing_2026"] = gw(bi[2026], 1, False)

    # validation
    v = val.filter(pl.col("ba") != "DOPD")
    d["shape_mape"] = rng(v["shape_mape_pct"].min(), v["shape_mape_pct"].max(), 1) + "%"
    d["n_bas"] = str(len(val))

    # prices
    def bra_val(yr: str, col: str) -> str:
        r = bra.filter(pl.col("delivery_year") == yr)
        return f"${r[col][0]:,.0f}"

    d["bra_2425"] = bra_val("2024/25", "rto_price")
    d["bra_2526"] = bra_val("2025/26", "rto_price")
    d["bra_2526_dom"] = bra_val("2025/26", "dom_price")

    # dates
    h = rank["DOM"]
    l0, l1 = date.fromisoformat(h["last12_from"]), date.fromisoformat(h["last12_to"])
    d["last12"] = f"{l0:%B %Y} – {l1:%B %Y}"
    d["cutoff"] = cutoff().strftime("%-d %B %Y")
    return Numbers(d)


def cutoff() -> date:
    """Last hour of demand used (UTC date)."""
    p = OUTPUTS_DIR / "counterfactual_hourly.parquet"
    ts = pl.scan_parquet(p).select(pl.col("period_end_utc").max()).collect().item()
    return ts.date()


def _md(df: pl.DataFrame, align: str | None = None) -> str:
    cols = df.columns
    al = align or ("l" + "r" * (len(cols) - 1))
    sep = ["---:" if a == "r" else ":---" for a in al]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(sep) + " |"]
    for r in df.iter_rows():
        lines.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    return "\n".join(lines)


def growth_table() -> str:
    """Headline growth since 2022 with intervals, the two model checks and beyond-trend."""
    rank = _csv("growth_ranking").filter(pl.col("headline"))
    dec = _rows(_csv("growth_decomposition"))
    rows = []
    for r in rank.iter_rows(named=True):
        b = r["ba"]
        if b not in NAMES or b in ("AZPS", "SRP"):
            continue
        name = NAMES[b] + (" ¹" if b in ("FPL", "SOCO") else "") + (" ²" if b == "AZPS+SRP" else "")
        rows.append(
            {
                "Region": name,
                "Group": ROLE.get(b, OTHER),
                "Growth since 2022": f"{pct(r['growth_pct'])} ({ci(r['growth_lo'], r['growth_hi'])})",
                "Avg MW added": num(r["mw_added"], 0, True),
                "LightGBM / degree-day": f"{pct(r['growth_gbm_pct'])} / {pct(r['growth_ddr_pct'])}",
                "Beyond own 2016–19 trend": pct(r["beyond_trend_pct"]),
                "Night ÷ afternoon MW": (
                    f"{dec[b]['night_to_afternoon']:.2f}"
                    if b in dec and dec[b]["verdict"] != "no growth"
                    else "no growth"
                ),
            }
        )
    return _md(pl.DataFrame(rows), "llrrrrr")


def robustness_table() -> str:
    grid = _csv("robustness_grid")
    labels = {
        "gam_2016_19": "Headline",
        "gbm": "LightGBM",
        "ddr": "Degree-day",
        "gam_2017_19": "2017–19 base",
        "gam_start_yr": "Fit on 2022",
        "gam_emp": "+ employment",
    }
    rows = []
    for b in [*DC, "PJM", "SOCO", "CISO", "MISO", "CPLE", "NYIS"]:
        r = grid.filter(pl.col("ba") == b).row(0, named=True)
        row = {"Region": NAMES[b]}
        row.update({v: f"{r[k]:.1f}" for k, v in labels.items()})
        rows.append(row)
    return _md(pl.DataFrame(rows))


def medians_table() -> str:
    med = _csv("robustness_medians")
    names = {
        "dc6_vs_d10": "Six data-center regions vs 8 comparisons",
        "prompt2_roles": "All 10 pre-registered focus regions vs 9 comparisons",
    }
    rows = []
    for gname, label in names.items():
        g = med.filter(pl.col("grouping") == gname)
        h = g.filter((pl.col("spec") == "gam_2016_19") & ~pl.col("excl_boundary_bas")).row(
            0, named=True
        )
        rows.append(
            {
                "Grouping": label,
                "Headline medians": f"{h['dc_median_pct']:.1f}% vs {h['comp_median_pct']:.1f}%",
                "Gap, headline": f"{h['gap_pts']:.1f} pts",
                "Gap, all specs": f"{g['gap_pts'].min():.1f}–{g['gap_pts'].max():.1f} pts",
                "Ratio, all specs": f"{g['ratio'].min():.1f}–{g['ratio'].max():.1f}×",
            }
        )
    return _md(pl.DataFrame(rows), "lrrrr")


def validation_table() -> str:
    v = _csv("validation_flags").sort("ba")
    gam = v.filter(pl.col("model") == "gam")
    ddr = _rows(v.filter(pl.col("model") == "ddr"))
    names = {
        **NAMES,
        "DOPD": "Douglas County PUD",
        "PSCO": "Public Service Co. of Colorado",
        "LGEE": "LG&E and KU",
        "SWPP": "Southwest Power Pool",
    }
    rows = []
    for r in gam.iter_rows(named=True):
        b = r["ba"]
        rows.append(
            {
                "BA": b,
                "Name": names.get(b, b),
                "MAPE": f"{r['mape_pct']:.1f}%",
                "Shape MAPE (GAM / OLS)": f"{r['shape_mape_pct']:.1f}% / {ddr[b]['shape_mape_pct']:.1f}%",
                "R²": f"{r['r2']:.2f}",
                "Residual trend": num(r["trend_pct_per_yr"], 1, True) + "%/yr",
                "Status": r["status"],
            }
        )
    return _md(pl.DataFrame(rows), "llrrrrl")


def utility_table() -> str:
    n = numbers()
    rows = [
        (
            "Dominion",
            "Metered data-center coincident demand, 2022→2025",
            n.dc_metered_22_25,
            f"Zone residual {n.dom_resid_22_25}",
            f"≈ {n.dc_share_22_25}",
        ),
        (
            "Dominion",
            "Data-center billing demand, 2019→2025",
            n.dc_billing_19_25,
            f"Zone residual {n.dom_resid_19_25}",
            f"≈ {n.dc_share_19_25}",
        ),
        (
            "ERCOT",
            "All approved large loads, observed peak (Mar 2026)",
            "3,883 MW (level)",
            f"Growth since 2022: {n.erco_mw}",
            "≈ 41%",
        ),
        (
            "APS",
            "Data-center share of 2026 peak growth",
            "≈ 200 MW",
            f"Phoenix: {n.azps_srp_mw}",
            "partial",
        ),
        (
            "Grant County PUD",
            "Data-center load 2025",
            "280 aMW (level)",
            f"Residual vs baseline 2025: {n.gcpd_resid_2025}",
            "most predates 2022",
        ),
    ]
    return _md(
        pl.DataFrame(
            rows, schema=["Utility", "Their figure", "Value", "Ours", "Share"], orient="row"
        ),
        "lllll",
    )


# ---------------------------------------------------------------- figures


def alt_text(stem: str) -> str:
    svg = FIGURES_DIR / f"{stem}.svg"
    m = re.search(r"<dc:description>(.*?)</dc:description>", svg.read_text(), re.S)
    return m.group(1).strip() if m else stem


def fig(stem: str, caption: str = "", rel: str = "figures") -> str:
    """Light and dark variants of a figure; Quarto shows the one matching the page theme."""
    if not (FIGURES_DIR / f"{stem}.png").exists():
        raise FileNotFoundError(f"{stem}.png: run `make figures`")
    alt = escape(alt_text(stem), quote=True).replace("\n", " ")
    cap = f"\n<figcaption>{caption}</figcaption>" if caption else ""
    # each variant links to its full-size PNG, so phone readers can zoom into a wide chart
    img = "".join(
        f'<a href="{rel}/{stem}{sfx}.png" class="{cls}" target="_blank">'
        f'<img src="{rel}/{stem}{sfx}.png" class="img-fluid" alt="{alt}"></a>'
        for sfx, cls in (("", "light-content"), ("_dark", "dark-content"))
    )
    img += '<div class="glg-zoom-hint">Tap the chart to open it full size.</div>'
    return f'\n\n<figure class="glg-figure" id="fig-{stem}">\n{img}{cap}\n</figure>\n\n'


def report_dir() -> Path:
    return FIGURES_DIR.parent
