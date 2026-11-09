# Social posts (drafts)

Links:
- Report: https://johnpospisil.github.io/datacenter-grid-load/
- Explorer: https://johnpospisil.github.io/datacenter-grid-load/explorer/
- Code: https://github.com/johnpospisil/datacenter-grid-load

Data through 22 October 2026. Wording rules (from the review): say "consistent with" data-center load, never "caused by"; give the region comparison as a range (2–10 points); ERCOT's large-load figure is a guide (~41%), not a cap; prices are context.

## Images

All in `report/social/` (built by `make social` from the published charts; light theme).

| File | Size | Use |
|---|---|---|
| `thread/01_hero_card.png` | 1200 × 675 | Thread post 1; LinkedIn; Reddit |
| `thread/02_chart2_card.png` | 1200 × 675 | Thread post 2 |
| `thread/03_chart8_dominion_metered.png` | 1600 × 900 | Thread post 3 |
| `thread/04_hero.png` | 1600 × 900 | Thread post 4 |
| `thread/05_chart4_supply_gap.png` | 1200 × 1200 | Thread post 5 |
| `thread/06_chart6_pjm_capacity.png` | 1600 × 900 | Thread post 6 |
| `thread/07_robustness_forest.png` | 1200 × 1200 | Thread post 7 |
| `og_card.png` | 1200 × 630 | Link preview (set as the report's and explorer's Open Graph image; HN, LinkedIn and Bluesky link cards use it automatically) |

16:9 and 1:1 both display uncropped on Bluesky, X, Reddit and LinkedIn. Alt text for each image is in the chart's SVG metadata and in the alt-text lines below.

---

## Hacker News: Show HN

**Title** (≤ 80 characters):

> Show HN: Weather-adjusting U.S. grid demand to find where data centers show up

**URL:** the report link.

**First comment:**

> I wanted to know how much U.S. electricity demand has actually grown since 2022, as opposed to how much utilities forecast. The hard part is weather: a hot summer looks like growth.
>
> So for 23 balancing authorities I fitted a model per region (a GAM: temperature × hour of day, recent-temperature inertia, humidity, season, day type) on 2016–2019 hourly EIA-930 demand with population-weighted ERA5 weather. It has no time trend. Then I predicted every hour since and treated actual ÷ predicted as structural growth.
>
> What came out:
>
> - Dominion's zone in Virginia (home of Loudoun County's data-center cluster) is up 24% since 2022 (18–24% across model specifications), about +3.2 GW of average demand.
> - It's the same +3.2 GW at 3 am as at 3 pm. Air-conditioning and offices don't do that; data centers do (so do crypto mines and fabs, which matters for Texas and Phoenix).
> - Dominion's own metered data-center demand accounts for roughly 60–73% of what I measure for the zone.
> - Regions with big data-center build-outs grew 2–10 points faster than comparison regions. The range depends on how you group regions, and I report both my grouping and one written down before I saw any weather-adjusted result.
> - New local generation in Dominion's zone covers ~5% of the new demand.
>
> Things I'd like pushback on: the Dominion zone's public EIA-930 history only starts in Sept 2018, so its baseline is short. The comparison-region choice is also a judgment call. And "flat across the day" is necessary but not sufficient evidence.
>
> Everything is reproducible (`make all`). Headline numbers are pinned in tests, and every cleaning rule and data problem I hit is written up in DATA.md. There's also a static explorer for each region. Code: https://github.com/johnpospisil/datacenter-grid-load

---

## r/energy

**Title:**

> I removed the weather from 10 years of hourly U.S. grid demand. Virginia's Dominion zone is up 24% since 2022, and it's the same at 3 am as at 3 pm

**Post** (link post to the report, or image post with `thread/01_hero_card.png` and the link in the first line):

> Report: https://johnpospisil.github.io/datacenter-grid-load/ · Explorer for every region: https://johnpospisil.github.io/datacenter-grid-load/explorer/
>
> **Method:** one model per balancing authority, fitted on 2016–2019 EIA-930 hourly demand against population-weighted ERA5 weather and the calendar, with no time trend. Growth is actual ÷ what that 2016–19 relationship predicts. COVID is excluded from the baseline.
>
> **Findings** (2022 → the 12 months to Oct 2026):
>
> - **Dominion zone: +24%** (+3.2 GW average), with +3.24 GW at night and +3.23 GW in the afternoon. Dominion's metered data-center demand explains roughly 60–73% of it; co-ops in the zone serve data centers too.
> - **Data-center regions vs comparison regions:** 2–10 points faster, depending on the grouping.
> - **ERCOT +19.5%** (11–20% across model specifications), but the large loads ERCOT tracks peak at only ~41% of that. Population growth and oil-field electrification matter there too.
> - **Phoenix +14%** (to Aug 2025, because EIA-930 and APS's FERC filing disagree after that). The flat floor could be fabs as well as data centers.
> - **Supply:** Dominion's zone added local generation worth ~1.4 TWh/yr against +28 TWh/yr of new demand. PJM capacity prices went from $29 to $270/MW-day, then to the ~$325–333 cap (context only, not a causal claim).
>
> **Caveats** are in the report: short Dominion baseline, rooftop solar, behind-the-meter load, preliminary recent data. I'd especially welcome corrections from people who work with EIA-930 or PJM load data.

---

## Bluesky / X thread (7 posts, one chart each)

Each post is ≤ 280 characters (X limit; Bluesky allows 300).

**1/7** · image `thread/01_hero_card.png`

> Electricity demand in Dominion's grid zone in Virginia is up 24% since 2022, after removing the effect of weather. Comparable regions: about +3%.
>
> I built an open, weather-adjusted measure of U.S. grid demand to see where data-center load is actually showing up. 🧵

Alt: Line chart of Dominion's weather-adjusted demand index rising from 100 in 2019 to 141 in 2026, while the median of comparison regions stays near 100–104.

**2/7** · image `thread/02_chart2_card.png`

> The new load runs around the clock: +3.24 GW at night, +3.23 GW in the afternoon.
>
> Air-conditioning and offices add load in the afternoon. A data center draws the same at 3 am as at 3 pm.

Alt: Dominion's night-time and afternoon weather-adjusted demand indices both rising steadily since 2022.

**3/7** · image `thread/03_chart8_dominion_metered.png`

> Dominion's own metered data-center demand grew about 1.4 GW from 2022 to 2025, roughly 60% of the growth I measure for the zone (73% since 2019).
>
> The zone also includes co-ops that serve data centers, so the two needn't match. Consistent with, not proof.

Alt: Line chart comparing the change since 2022 in Dominion's metered data-center demand with the weather-adjusted growth estimate for the Dominion zone; both rise, the estimate faster.

**4/7** · image `thread/04_hero.png`

> The ten fastest-growing regions. Texas grew ~20% (11–20% across models), but the large loads ERCOT tracks peak at only ~41% of that.
>
> Phoenix +14%; its flat floor fits data centers and chip fabs alike. The Pacific Northwest is unverified.

Alt: Ten small line charts of weather-adjusted demand indexed to 2019 = 100; Dominion reaches 141, ERCOT 131, Phoenix 120, while the comparison median stays near 100–105.

**5/7** · image `thread/05_chart4_supply_gap.png`

> Is supply keeping up? In Dominion's zone, new local generation adds ~1.4 TWh/yr of expected energy against +28 TWh/yr of new demand: about 5%.
>
> The rest comes from imports and existing plants running harder.

Alt: Bar chart of new local generation minus weather-adjusted demand growth since 2022, by region; Dominion's gap is the largest relative to its size.

**6/7** · image `thread/06_chart6_pjm_capacity.png`

> Context, not causation: PJM's capacity auction price went from $29 to $270 per MW-day, then cleared at the cap three times. The last two auctions fell 6.5–6.8 GW short of PJM's reliability target.

Alt: Step chart of PJM capacity auction prices by delivery year, low through 2024/25, jumping in 2025/26, then flat at the price cap.

**7/7** · image `thread/07_robustness_forest.png`

> Robustness: Dominion leads under every model, baseline and control I tried (18–24%). Data-center regions beat comparisons by 2–10 points, depending on grouping.
>
> Report: https://johnpospisil.github.io/datacenter-grid-load/
> Code: https://github.com/johnpospisil/datacenter-grid-load

Alt: Forest plot of weather-adjusted growth since 2022 for each region under seven model specifications; Dominion is highest under all of them.

---

## LinkedIn (framed around the method)

Image: `thread/01_hero_card.png` (or post the report link and let `og_card.png` show as the preview).

> **How do you measure demand growth when the weather keeps moving the goalposts?**
>
> Utilities are forecasting a surge in electricity demand from AI data centers. I wanted to measure what has *already* happened, which means separating growth from weather. A hot summer looks a lot like new customers.
>
> The approach:
> 🔹 10 years of hourly demand for 23 U.S. grid regions (EIA-930), cleaned with documented rules
> 🔹 Population-weighted hourly weather per region from ERA5 reanalysis, mapped through utility service territories to counties
> 🔹 One interpretable model per region (a generalised additive model) fitted on 2016–2019, with no time trend, validated on held-out years
> 🔹 Structural growth = actual ÷ what the 2016–19 relationship predicts
> 🔹 A load-shape test: data centers add the same MW at 3 am as at 3 pm
> 🔹 Six alternative specifications (LightGBM, degree-day OLS, other baselines, an employment control), plus a grouping written down before any weather-adjusted result
>
> The result: weather-adjusted demand in Dominion's zone in Virginia is up 24% since 2022, added evenly around the clock. Dominion's own metered data-center demand accounts for roughly 60–73% of it. Regions with large data-center build-outs grew 2–10 points faster than comparison regions, and the range is the honest answer, because it depends on the grouping.
>
> What I'd do the same way again: pin every headline number in tests, and write down how the finding could be wrong before looking.
>
> Report, interactive explorer and reproducible code: https://johnpospisil.github.io/datacenter-grid-load/
>
> #DataScience #Energy #ElectricGrid #DataCenters #OpenData
