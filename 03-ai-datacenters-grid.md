# Project 3: AI Data Centers and the Power Grid

**Working title:** *The Load Nobody Planned For: Weather-Adjusted Electricity Demand Growth Across U.S. Grids, 2016–Today*

**One-sentence pitch:** Strip the effect of weather out of a decade of hourly U.S. electricity demand, find which grid regions show real structural load growth after years of flat demand, test whether it lines up with data-center build-outs, and compare it with how fast new generation is (or isn't) connecting to the grid.

---

## 1. The question

**Primary:** After adjusting for weather, calendar and economic activity, which U.S. balancing authorities (and PJM sub-zones such as Dominion/Northern Virginia) show **structural demand growth since 2022**, and how big is it compared with the flat 2010s?

**Secondary:**
- Has the *shape* of demand changed? Data centers draw near-constant 24/7 load, so a rising overnight floor (and a flatter load curve) is a fingerprint you can test for.
- Is new generation keeping up? Compare demand growth with capacity added and capacity stuck in interconnection queues, by region.
- Is it showing up in prices: wholesale (capacity auctions, real-time prices) and retail (EIA state retail rates)?

**A finding that would be wrong if:** weather-adjusted demand in data-center-heavy regions grows no faster than in comparison regions, or growth appears only during daytime/peak hours (pointing to electrification or economic growth rather than data centers).

## 2. Why now

- After roughly 15 years of flat U.S. electricity demand, utilities and grid operators are forecasting rapid growth, largely attributed to data centers for AI. LBNL's 2024 *United States Data Center Energy Usage Report* estimated data centers at about 4.4% of U.S. electricity in 2023, with projections of roughly 7–12% by 2028.
- PJM's capacity auction prices jumped sharply in the 2025/26 and 2026/27 auctions, and grid operators cited data-center load. This has become a story about consumer power bills, which reaches a much wider audience than energy specialists.
- Most coverage repeats utility *forecasts*. An independent, **weather-normalised measurement of what has actually happened**, published as an open pipeline, is rare and useful.

## 3. Data sources

| Source | What it gives you | Grain | Where |
|---|---|---|---|
| **EIA-930 Hourly Electric Grid Monitor** | Demand, demand forecast, net generation by fuel, interchange, for every balancing authority (BA); subregion data for some large BAs (e.g. PJM zones, ERCOT weather zones) | BA × hour, July 2015–present | eia.gov/electricity/gridmonitor + EIA API v2 |
| **EIA-860M** | Monthly generator inventory: operating, planned, retired, with capacity and location | generator × month | eia.gov/electricity/data/eia860m |
| **EIA-861 / 861M** | Utility retail sales, customers, revenue → retail price by state and sector | utility × year/month | eia.gov/electricity/data/eia861 |
| **LBNL "Queued Up"** | Interconnection queue projects: type, MW, queue date, status, region | project, annual release | emp.lbl.gov/queues |
| **NOAA ISD / GHCN hourly** or **ERA5 reanalysis** | Hourly temperature and humidity → population-weighted heating/cooling degree hours per BA | station / grid cell × hour | ncei.noaa.gov; Copernicus CDS (ERA5) |
| **Census population grids / county pop** | Population weights for temperature | county | census.gov |
| **PJM / ERCOT / MISO market data** | Capacity auction results, real-time LMPs, official load forecasts | zone × hour / auction | PJM Data Miner, ERCOT MIS, gridstatus (open-source Python library, BSD) |
| **Data-center location proxies** | No single authoritative public dataset. Options: utility IRP filings, state economic-development announcements, county tax-abatement records, public trackers. Treat as **context**, not ground truth | facility / county | Build a small hand-curated, cited table |

**Tooling tip:** the open-source `gridstatus` Python library wraps many ISO data feeds; check its license and cite it in your notices.

**Expected size:** EIA-930 is about 65 BAs × ~90k hours ≈ 6M rows. Weather data is larger; aggregate early to BA × hour.

## 4. Building the dataset

1. **Ingest** EIA-930 for every BA and subregion via the API; cache raw JSON/CSV.
2. **Clean:** EIA-930 has gaps, spikes and occasional unit errors. Flag them with rules (negative demand, > 5σ hour-over-hour jumps, stretches of repeated values) and with EIA's own imputed-value flags where present. Document every rule and keep both raw and cleaned columns.
3. **Weather features:** map each BA to its service-area counties; compute population-weighted hourly temperature, plus cooling/heating degree hours and humidity. Validate the mapping on a few known BAs.
4. **Calendar features:** hour, day-of-week, holidays, DST transitions.
5. **Supply side:** aggregate EIA-860M additions/retirements and LBNL queue MW by BA/ISO and year.
6. **Checks** (`tests/`): annual demand totals within a few % of EIA's published annual figures; no duplicated hours; DST hours handled; weather coverage ≥ 95% of hours.

## 5. Method

### Weather normalisation (the core)
- For each BA, fit a model of hourly demand on weather and calendar features using a **baseline period (e.g. 2016–2019)**. Gradient boosting (LightGBM) or a GAM (`pygam`): start with the GAM because it's easier to interpret, then compare.
- Predict "what demand would have been under the baseline relationship" for every hour since; **structural growth = actual − predicted**, aggregated by month and year.
- Validate on held-out baseline years: report MAPE per BA and show that residuals stay flat during the baseline period (if they drift, the model misses something).
- Handle COVID (2020–21) explicitly: exclude it from the baseline and mark it on every chart.

### Fingerprinting data-center load
- **Load-shape metrics** per BA and year: overnight minimum, the ratio of minimum to mean, and load factor. Steady 24/7 load raises the floor more than the peak.
- **Comparison:** data-center-heavy zones (Dominion in PJM, parts of ERCOT, Phoenix/SRP/APS, Georgia Power) vs similar-climate zones without large build-outs. Treat this as descriptive comparison, not tight causal identification.
- **Decompose growth** into a constant 24/7 component and a weather-sensitive component per BA.

### Supply vs demand
- For each region: weather-adjusted demand growth (TWh/yr and average MW) next to MW of new capacity actually online and MW in queues, adjusted for capacity factor so you compare energy with energy.
- Price context: capacity auction clearing prices and state retail price trends, plotted alongside. Explicitly **not** a causal model of prices.

### Robustness
- Alternative baselines (2016–2019 vs 2017–2019 vs 2022 only).
- Alternative model families (GAM vs GBM vs a simple degree-day regression).
- Account for big BA boundary or definition changes, which do happen in EIA-930.
- Compare your growth estimates with utilities' own reported figures where available.

## 6. Charts

| # | Chart | Purpose |
|---|---|---|
| **Hero** | **Weather-adjusted demand growth, indexed (2019 = 100)**, top 10 BAs as small multiples, 2016–today, COVID shaded, labelled annotations for major data-center announcements | The headline: where load stopped being flat |
| 2 | **Rising floor:** overnight minimum load by year for Dominion vs a comparison zone (ridgeline or line chart) | The data-center fingerprint |
| 3 | Heat calendar (day × hour) of residual demand for Dominion, year by year | Shows the steady 24/7 shift |
| 4 | Diverging bar: weather-adjusted growth vs new generation added, by region (TWh) | Is supply keeping up? |
| 5 | Queue backlog: MW in queue by technology and year, by ISO (stacked bar) | Explains *why* supply lags |
| 6 | Capacity auction price history (PJM) as a step chart with annotated events | Connects to consumer bills |
| 7 | Model validation: predicted vs actual on held-out baseline year, residual time series | Builds trust; goes in the methods appendix |

**Interactive:** pick a balancing authority → see actual vs weather-normalised demand, load-shape metrics and its supply pipeline. Optionally update monthly with a scheduled GitHub Action, which turns the project into a "living tracker" that people come back to.

## 7. Repo skeleton

```
grid-load-growth/
  README.md
  DATA.md                  # BA definitions, cleaning rules, weather mapping, known EIA-930 issues
  Makefile
  pyproject.toml, uv.lock
  src/glg/
    ingest/eia930.py, eia860m.py, eia861.py, queues.py, weather.py, iso_markets.py
    build/clean.py, ba_weather_map.py, features.py
    models/normalize_gam.py, normalize_gbm.py, load_shape.py, supply_gap.py
    viz/theme.py, figures.py
  data/reference/ba_county_map.csv, datacenter_announcements.csv (cited)
  tests/                   # annual totals vs EIA, DST handling, pinned growth estimates
  notebooks/
  report/index.qmd
  app/
  .github/workflows/monthly_refresh.yml   # optional living tracker
```

## 8. Milestones

| Week | Deliverable |
|---|---|
| 1 | EIA-930 ingest + cleaning rules + tests; annual totals reconcile |
| 2 | BA → county mapping; weather features for all BAs |
| 3 | Baseline normalisation models; validation metrics per BA |
| 4 | Structural growth estimates; hero chart v1; load-shape fingerprint |
| 5 | Supply side (860M, queues) and price context |
| 6 | Robustness checks; article draft; BA explorer app |
| 7 | Review by someone in the energy industry; publish; optional monthly refresh |

## 9. Distribution
- Energy readers are very active online: Hacker News, r/energy, r/electricity, the "energy Twitter/Bluesky" community, and newsletters such as Heatmap and Latitude Media. A clean, novel chart travels well there.
- Local angle: Virginia, Texas, Georgia and Arizona reporters cover data-center power fights constantly. Send them region-specific charts.
- A monthly-updating tracker gets repeat visits and links, which a one-off notebook never will.

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| EIA-930 data quality (gaps, spikes, boundary changes) | Documented cleaning rules, raw vs cleaned columns, sensitivity checks |
| Can't directly observe data-center load | Present the fingerprint as consistent with data centers, not proof; compare with utility disclosures |
| Other growth drivers (EVs, heat pumps, manufacturing reshoring, crypto mining) | Load-shape decomposition; discuss each alternative; ERCOT crypto is a known confounder |
| Weather model misspecification | Multiple model families; held-out validation; residual diagnostics |
| Price claims overreach | Keep prices descriptive; explain capacity markets plainly |

## 11. Stretch goals
- Emissions angle: marginal fuel mix in hours with high residual demand (EIA-930 generation by fuel). Is new load being met by gas?
- Short-term forecast of next year's demand per BA, with prediction intervals, scored publicly as data arrives.
- International comparison (Ireland's EirGrid publishes data-center consumption explicitly, a useful benchmark).
