/* Grid region explorer (Prompt 18). Static: reads data/index.json and data/series/<slug>.json,
   built by `python -m glg.viz.explorer`. Charts: Observable Plot. Colours come from the CSS
   tokens in style.css, which mirror src/glg/viz/theme.py. */
"use strict";

const GROUP_LABEL = {
  "data-center": "Data-center region",
  comparison: "Comparison region",
  other: "Other region",
  part: "Part of a combined series",
};
const GROUP_MENU = [
  ["data-center", "Data-center regions"],
  ["other", "Other regions"],
  ["comparison", "Comparison regions"],
  ["part", "Parts of Phoenix"],
];

const state = { index: null, cache: new Map(), view: null, band: "all" };
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- formatting
const minus = (s) => s.replace(/-/g, "−");
const fmt = (x, nd = 1, sign = false) =>
  x == null ? "–" : minus((sign && x > 0 ? "+" : "") + x.toLocaleString("en-US", { minimumFractionDigits: nd, maximumFractionDigits: nd }));
const pct = (x, nd = 1, sign = true) => (x == null ? "–" : fmt(x, nd, sign) + "%");
const mw = (x, sign = true) => (x == null ? "–" : fmt(x, 0, sign) + " MW");
const parseMonth = (m) => new Date(Date.UTC(+m.slice(0, 4), +m.slice(5, 7) - 1, 1));
const monthName = (m) => parseMonth(m).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function tokens() {
  const cs = getComputedStyle(document.documentElement);
  const v = (k) => cs.getPropertyValue("--" + k).trim();
  return Object.fromEntries(
    ["bg", "ink", "muted", "faint", "rule", "covid", "other", "amber", "magenta", "sky", "green", "vermillion", "blue"].map((k) => [k, v(k)])
  );
}

function groupColor(t, g) {
  return { "data-center": t.blue, comparison: t.other, other: t.sky, part: t.sky }[g];
}

function legend(el, items) {
  el.innerHTML = items
    .map(([label, color, kind = "line"]) => {
      const cls = kind === "line" ? "" : kind;
      const style = kind === "box" ? `background:${color}` : `border-color:${color}`;
      return `<span><i class="${cls}" style="${style}"></i>${esc(label)}</span>`;
    })
    .join("");
}

function width(el) {
  return Math.max(300, Math.floor(el.getBoundingClientRect().width || 600));
}

function basePlot(t, el, opts) {
  return {
    width: width(el),
    height: Math.round(Math.min(360, Math.max(240, width(el) * 0.5))),
    marginLeft: 48,
    marginRight: 44,
    marginTop: 14,
    marginBottom: 30,
    style: { background: "transparent", color: t.ink, fontSize: "12px", fontFamily: "inherit", overflow: "visible" },
    ...opts,
  };
}

function covidMarks(t, index) {
  const [a, b] = index.covid;
  const end = parseMonth(b);
  end.setUTCMonth(end.getUTCMonth() + 1);
  return [Plot.rect([{ x1: parseMonth(a), x2: end }], { x1: "x1", x2: "x2", fill: t.covid })];
}

function tip(t, data, opts) {
  return Plot.tip(data, Plot.pointerX({ ...opts, fill: t.bg, stroke: t.rule, textPadding: 6 }));
}

function crosshair(t, data, x) {
  return Plot.ruleX(data, Plot.pointerX({ x, stroke: t.muted, strokeOpacity: 0.5 }));
}

function mount(el, fig) {
  el.replaceChildren(fig);
  return fig;
}

// ---------------------------------------------------------------- data
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function series(slug) {
  if (!state.cache.has(slug)) state.cache.set(slug, getJSON(`data/series/${slug}.json`));
  return state.cache.get(slug);
}

// ---------------------------------------------------------------- overview (phone-friendly hero)
function renderOverview() {
  const t = tokens();
  const idx = state.index;
  const night = state.band === "night";
  const rows = idx.regions
    .filter((r) => r.group !== "part")
    .map((r) => ({ ...r, v: night ? r.night_growth_pct : r.growth_pct }))
    .filter((r) => r.v != null)
    .sort((a, b) => b.v - a.v);
  $("ov-sub").textContent =
    `From calendar 2022 to the latest 12 months (FPL and Southern from 2023; Phoenix to August 2025). ` +
    `Median data-center region ${pct(idx.dc_median_pct)}, median comparison region ${pct(idx.comp_median_pct)} (all hours).`;
  legend($("ov-legend"), [
    ["Data-center region", groupColor(t, "data-center"), "dot"],
    ["Comparison region", groupColor(t, "comparison"), "dot"],
    ["Other region", groupColor(t, "other"), "dot"],
  ]);
  const el = $("ov-chart");
  const w = width(el);
  const narrow = w < 520;
  const fig = Plot.plot({
    width: w,
    height: rows.length * 26 + 50,
    marginLeft: narrow ? 118 : 150,
    marginRight: 20,
    marginTop: 26,
    marginBottom: 28,
    style: { background: "transparent", color: t.ink, fontSize: narrow ? "12px" : "13px", fontFamily: "inherit" },
    x: { label: night ? "Night growth, % →" : "Growth, % →", grid: true, labelAnchor: "right", tickFormat: (d) => minus(String(d)) },
    y: { domain: rows.map((r) => r.short), label: null, tickSize: 0 },
    marks: [
      Plot.gridX({ stroke: t.faint, strokeOpacity: 1 }),
      Plot.ruleX([0], { stroke: t.rule }),
      night ? null : Plot.ruleY(rows, { y: "short", x1: "growth_lo", x2: "growth_hi", stroke: (d) => groupColor(t, d.group), strokeWidth: 3, strokeOpacity: 0.45 }),
      Plot.dot(rows, { x: "v", y: "short", r: 5, fill: (d) => groupColor(t, d.group), stroke: t.bg, strokeWidth: 2 }),
      Plot.text(rows, { x: "v", y: "short", text: (d) => pct(d.v), dx: 10, textAnchor: "start", fill: t.muted, fontSize: 11, filter: (d) => d.v >= 0 && !narrow }),
      Plot.tip(
        rows,
        Plot.pointerY({
          x: "v",
          y: "short",
          fill: t.bg,
          stroke: t.rule,
          title: (d) => `${d.name}\n${night ? "Night growth" : "Growth"}: ${pct(d.v)}${night ? "" : ` (${pct(d.growth_lo, 1, false)}–${pct(d.growth_hi, 1, false)})`}\n${mw(d.mw_added)} average\nTap to open`,
        })
      ),
    ],
  });
  fig.setAttribute("role", "img");
  fig.setAttribute("aria-label", `Ranked dot chart of weather-adjusted ${night ? "night-time " : ""}growth since 2022 for ${rows.length} regions; ${rows[0].name} is highest at ${pct(rows[0].v)}.`);
  fig.addEventListener("click", () => fig.value && go(fig.value.slug));
  mount(el, fig);

  $("ov-table").innerHTML =
    `<thead><tr><th>Region</th><th>Group</th><th>Growth since 2022</th><th>95% interval</th><th>Night growth</th><th>Avg MW added</th></tr></thead><tbody>` +
    rows
      .map(
        (r) =>
          `<tr><td><a href="#/${r.slug}">${esc(r.name)}</a></td><td>${esc(r.group)}</td><td>${pct(r.growth_pct)}</td>` +
          `<td>${pct(r.growth_lo, 1, false)} to ${pct(r.growth_hi, 1, false)}</td><td>${pct(r.night_growth_pct)}</td><td>${fmt(r.mw_added, 0, true)}</td></tr>`
      )
      .join("") +
    "</tbody>";
}

// ---------------------------------------------------------------- region view
function tiles(s) {
  const items = [
    [`${pct(s.growth_pct)}`, `Weather-adjusted growth since ${s.from_year} (95%: ${pct(s.growth_lo, 1, false)} to ${pct(s.growth_hi, 1, false)})`],
    [mw(s.mw_added), "Average demand added"],
    [pct(s.beyond_trend_pct), "Beyond the region's own 2016–19 trend"],
    [s.night_to_afternoon == null ? "–" : fmt(s.night_to_afternoon, 2), `MW added at night ÷ afternoon${s.verdict ? ` · ${s.verdict}` : ""}`],
  ];
  if (s.spec_min != null) items.push([`${fmt(s.spec_min, 1)}–${pct(s.spec_max, 1, false)}`, "Range across model specifications (see report)"]);
  return items.map(([v, l]) => `<div class="tile"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`).join("");
}

function indexChart(t, d) {
  const idx = state.index;
  const cmp = new Map(idx.comparison_median_index.map((r) => [r.m, r.i]));
  const data = d.monthly.filter((r) => r.i != null).map((r) => ({ ...r, date: parseMonth(r.m), c: cmp.get(r.m) ?? null }));
  const showCmp = d.group !== "comparison";
  legend($("c-index-legend"), [
    [d.short, t.blue],
    ...(showCmp ? [["Median of comparison regions", t.other, "dash"]] : []),
    ["COVID (excluded from baseline)", t.covid, "box"],
    ...(d.annotations.length ? [["Utility or grid-operator disclosure", t.ink, "dot"]] : []),
  ]);
  const ann = d.annotations
    .map((a, i) => {
      const row = data.find((r) => r.m === a.m) || data[data.length - 1];
      return { ...a, n: i + 1, date: row.date, i: row.i };
    })
    .filter((a) => a.i != null);
  const el = $("c-index");
  const last = data[data.length - 1];
  const fig = Plot.plot(
    basePlot(t, el, {
      x: { type: "utc", label: null },
      y: { label: "Index, 2019 = 100", grid: true },
      marks: [
        ...covidMarks(t, idx),
        Plot.gridY({ stroke: t.faint, strokeOpacity: 1 }),
        Plot.ruleY([100], { stroke: t.rule }),
        Plot.areaY(data, { x: "date", y1: "lo", y2: "hi", fill: t.blue, fillOpacity: 0.18 }),
        showCmp ? Plot.lineY(data.filter((r) => r.c != null), { x: "date", y: "c", stroke: t.other, strokeWidth: 1.5, strokeDasharray: "4,3" }) : null,
        Plot.lineY(data, { x: "date", y: "i", stroke: t.blue, strokeWidth: 2 }),
        Plot.text([last], { x: "date", y: "i", text: (r) => fmt(r.i, 0), dx: 6, textAnchor: "start", fill: t.ink, fontWeight: 600 }),
        Plot.dot(ann, { x: "date", y: "i", r: 7, fill: t.bg, stroke: t.ink, strokeWidth: 1.5 }),
        Plot.text(ann, { x: "date", y: "i", text: "n", fill: t.ink, fontSize: 9, fontWeight: 700 }),
        crosshair(t, data, "date"),
        tip(t, data, {
          x: "date",
          y: "i",
          title: (r) =>
            `${monthName(r.m)}\nIndex ${fmt(r.i, 1)} (${fmt(r.lo, 1)}–${fmt(r.hi, 1)})` + (showCmp && r.c != null ? `\nComparison median ${fmt(r.c, 1)}` : ""),
        }),
      ],
    })
  );
  fig.setAttribute("role", "img");
  fig.setAttribute("aria-label", `Line chart: ${d.name} weather-adjusted demand index, trailing 12 months, reaching ${fmt(last.i, 0)} in ${monthName(last.m)} (2019 = 100).`);
  mount(el, fig);
  $("r-annot").innerHTML = ann.map((a) => `<li><strong>${a.n}</strong> · ${esc(monthName(a.m))}: ${esc(a.label)}</li>`).join("");
}

function mwChart(t, d) {
  const data = d.monthly.filter((r) => r.a != null && r.p != null).map((r) => ({ ...r, date: parseMonth(r.m) }));
  legend($("c-mw-legend"), [
    ["Actual", t.blue],
    ["Weather-normalised (2016–19 relationship)", t.other, "dash"],
    ["COVID", t.covid, "box"],
  ]);
  const el = $("c-mw");
  const fig = Plot.plot(
    basePlot(t, el, {
      marginLeft: 56,
      x: { type: "utc", label: null },
      y: { label: "Average MW", grid: true, tickFormat: "~s" },
      marks: [
        ...covidMarks(t, state.index),
        Plot.gridY({ stroke: t.faint, strokeOpacity: 1 }),
        Plot.lineY(data, { x: "date", y: "p", stroke: t.other, strokeWidth: 1.5, strokeDasharray: "4,3" }),
        Plot.lineY(data, { x: "date", y: "a", stroke: t.blue, strokeWidth: 2 }),
        crosshair(t, data, "date"),
        tip(t, data, {
          x: "date",
          y: "a",
          title: (r) =>
            `${monthName(r.m)}${r.h < 600 ? ` (${r.h} h of data)` : ""}\nActual ${mw(r.a, false)}\nWeather-normalised ${mw(r.p, false)}\nDifference ${pct(100 * (r.a / r.p - 1))}`,
        }),
      ],
    })
  );
  fig.setAttribute("role", "img");
  fig.setAttribute("aria-label", `Line chart: ${d.name} monthly actual demand against weather-normalised demand.`);
  mount(el, fig);
}

function bandChart(t, d) {
  const data = d.monthly.filter((r) => r.n != null && r.d != null).map((r) => ({ ...r, date: parseMonth(r.m) }));
  const el = $("c-band");
  if (!data.length) {
    el.textContent = "No time-of-day series for this region.";
    return;
  }
  legend($("c-band-legend"), [
    ["Night, 1–5 am local", t.blue],
    ["Afternoon, 2–7 pm local", t.vermillion],
    ["COVID", t.covid, "box"],
  ]);
  const last = data[data.length - 1];
  const ends = [
    { date: last.date, y: last.n, label: fmt(last.n, 0) },
    { date: last.date, y: last.d, label: fmt(last.d, 0) },
  ];
  const gap = Math.abs(last.n - last.d) < 3 ? 7 : 0; // nudge apart when the lines end close together
  const up = last.n >= last.d;
  const fig = Plot.plot(
    basePlot(t, el, {
      x: { type: "utc", label: null },
      y: { label: "Index, 2019 = 100", grid: true },
      marks: [
        ...covidMarks(t, state.index),
        Plot.gridY({ stroke: t.faint, strokeOpacity: 1 }),
        Plot.ruleY([100], { stroke: t.rule }),
        Plot.lineY(data, { x: "date", y: "d", stroke: t.vermillion, strokeWidth: 2 }),
        Plot.lineY(data, { x: "date", y: "n", stroke: t.blue, strokeWidth: 2 }),
        Plot.text([ends[0]], { x: "date", y: "y", text: "label", dx: 6, dy: up ? -gap : gap, textAnchor: "start", fill: t.ink, fontWeight: 600 }),
        Plot.text([ends[1]], { x: "date", y: "y", text: "label", dx: 6, dy: up ? gap : -gap, textAnchor: "start", fill: t.ink, fontWeight: 600 }),
        crosshair(t, data, "date"),
        tip(t, data, { x: "date", y: "n", title: (r) => `${monthName(r.m)}\nNight ${fmt(r.n, 1)}\nAfternoon ${fmt(r.d, 1)}` }),
      ],
    })
  );
  fig.setAttribute("role", "img");
  fig.setAttribute("aria-label", `Line chart: ${d.name} night-time and afternoon weather-adjusted demand index; latest night ${fmt(last.n, 0)}, afternoon ${fmt(last.d, 0)}.`);
  mount(el, fig);
  const s = d.summary;
  $("r-band-note").textContent =
    s.night_mw == null
      ? ""
      : `Since ${s.from_year}: ${mw(s.night_mw)} at night and ${mw(s.afternoon_mw)} in the afternoon. ` +
        `Of the change in average demand, ${mw(s.flat_mw)} is flat around the clock and ${mw(s.weather_mw)} responds to temperature.` +
        (d.pair ? ` Comparison region: ` : "");
  if (d.pair) {
    const p = state.index.regions.find((r) => r.code === d.pair);
    if (p) {
      const a = document.createElement("a");
      a.href = `#/${p.slug}`;
      a.textContent = `${p.name} (night ${pct(p.night_growth_pct)})`;
      $("r-band-note").append(a, ".");
    }
  }
}

function shapeTable(d) {
  const rows = d.annual.filter((r) => r.y >= 2016);
  $("r-shape").innerHTML =
    `<thead><tr><th>Year</th><th>Mean MW</th><th>Peak MW</th><th>Load factor</th><th>Daily min ÷ mean</th><th>Weather-adj. vs baseline</th><th>Night</th><th>Afternoon</th></tr></thead><tbody>` +
    rows
      .map((r) => {
        const covid = r.y === 2020 || r.y === 2021;
        const note = r.partial ? " (part)" : covid ? " (COVID)" : "";
        return (
          `<tr class="${r.partial ? "partial" : ""}"><td>${r.y}${note}</td><td>${fmt(r.mean_mw, 0)}</td><td>${fmt(r.peak_mw, 0)}</td>` +
          `<td>${fmt(r.load_factor, 2)}</td><td>${fmt(r.min_mean, 2)}</td><td>${pct(r.resid_pct)}</td><td>${pct(r.night_resid_pct)}</td><td>${pct(r.afternoon_resid_pct)}</td></tr>`
        );
      })
      .join("") +
    `</tbody><caption class="sub" style="caption-side:bottom;text-align:left;padding-top:6px">Load factor = mean ÷ peak. Daily min ÷ mean: average daily minimum over mean load (higher = flatter). Last three columns: actual ÷ weather-normalised demand − 1, for all hours, 1–5 am and 2–7 pm.</caption>`;
}

const TECHS = [
  ["solar", "Solar", "amber"],
  ["wind", "Wind", "sky"],
  ["storage", "Storage", "green"],
  ["gas", "Gas", "vermillion"],
  ["coal", "Coal", "blue"],
  ["other", "Other (incl. nuclear)", "other"],
];

function supplyChart(t, d) {
  const sup = d.supply;
  const el = $("c-supply");
  const s = sup.since;
  $("r-supply-sub").textContent = s
    ? `Generators added (up) and retired (down) per year, net summer MW (EIA-860M). Since ${d.summary.from_year}, expected local generation changed by ${fmt(s.supply_twh, 1, true)} TWh/yr ` +
      `against ${fmt(s.demand_twh, 1, true)} TWh/yr of weather-adjusted demand growth (batteries count as zero energy).`
    : "Generators added and retired per year, net summer MW (EIA-860M).";
  const long = [];
  for (const r of sup.years) {
    for (const [k, label] of TECHS) {
      const add = k === "other" ? r.add_other + r.add_nuclear : r[`add_${k}`];
      const ret = k === "other" ? r.ret_other + r.ret_nuclear : r[`ret_${k}`];
      if (add) long.push({ year: String(r.y) + (r.partial ? "*" : ""), tech: label, mw: add });
      if (ret) long.push({ year: String(r.y) + (r.partial ? "*" : ""), tech: label, mw: -Math.abs(ret) });
    }
  }
  if (!long.length) {
    el.textContent = "No generator changes recorded for this region.";
    $("c-supply-legend").innerHTML = "";
    return;
  }
  legend($("c-supply-legend"), TECHS.map(([, label, c]) => [label, t[c], "box"]));
  const years = [...new Set(sup.years.map((r) => String(r.y) + (r.partial ? "*" : "")))];
  const fig = Plot.plot(
    basePlot(t, el, {
      marginLeft: 56,
      x: { domain: years, label: null, tickFormat: (y) => (width(el) < 520 ? `'${y.slice(2)}` : y) },
      y: { label: "MW added (+) / retired (−)", grid: true, tickFormat: (v) => minus(d3.format("~s")(v)) },
      color: { domain: TECHS.map((x) => x[1]), range: TECHS.map((x) => t[x[2]]) },
      marks: [
        Plot.gridY({ stroke: t.faint, strokeOpacity: 1 }),
        Plot.barY(long, { x: "year", y: "mw", fill: "tech", insetLeft: 1, insetRight: 1, tip: { fill: t.bg, stroke: t.rule }, title: (r) => `${r.year} · ${r.tech}\n${r.mw > 0 ? "Added" : "Retired"} ${fmt(Math.abs(r.mw), 0)} MW` }),
        Plot.ruleY([0], { stroke: t.ink }),
      ],
    })
  );
  fig.setAttribute("role", "img");
  fig.setAttribute("aria-label", `Bar chart: ${d.name} generator capacity added and retired per year by technology.`);
  mount(el, fig);

  const q = d.queue;
  const qel = $("r-queue");
  if (!q) {
    qel.innerHTML = `<p class="sub">No interconnection-queue series for this region.</p>`;
    return;
  }
  const QT = [
    ["solar", "Solar", "amber"],
    ["hybrid", "Hybrid", "magenta"],
    ["wind", "Wind", "sky"],
    ["battery", "Storage", "green"],
    ["gas", "Gas", "vermillion"],
    ["other", "Other (incl. nuclear)", "other"],
  ];
  const qrows = QT.map(([k, label, c]) => ({ label, c: t[c], gw: k === "other" ? (q.by_type_gw.other || 0) + (q.by_type_gw.nuclear || 0) : q.by_type_gw[k] || 0 })).filter((r) => r.gw >= 0.05);
  const bits = [`<strong>${fmt(q.active_gw, 1)} GW</strong> waiting in the interconnection queue at the end of 2025 (${q.n_active} requests).`];
  if (q.completion_pct_mw != null) bits.push(`Historically ${fmt(q.completion_pct_mw, 0)}% of queued MW here reached operation.`);
  if (q.expected_twh != null) bits.push(`At that rate the queue would add about ${fmt(q.expected_twh, 1)} TWh/yr.`);
  qel.innerHTML = `<p class="sub">${bits.join(" ")}</p><div id="c-queue" class="chart"></div>`;
  const qfig = Plot.plot({
    width: width(qel),
    height: 64,
    marginLeft: 8,
    marginRight: 8,
    marginTop: 4,
    marginBottom: 22,
    style: { background: "transparent", color: t.ink, fontSize: "11px", fontFamily: "inherit" },
    x: { label: null },
    marks: [
      Plot.barX(qrows, Plot.stackX({ x: "gw", fill: (r) => r.c, insetLeft: 1, tip: { fill: t.bg, stroke: t.rule }, title: (r) => `${r.label}: ${fmt(r.gw, 1)} GW` })),
    ],
  });
  qfig.setAttribute("role", "img");
  qfig.setAttribute("aria-label", "Queue by type: " + qrows.map((r) => `${r.label} ${fmt(r.gw, 1)} GW`).join(", "));
  $("c-queue").replaceChildren(qfig);
  const lg = document.createElement("div");
  lg.className = "legend";
  legend(lg, qrows.map((r) => [`${r.label} ${fmt(r.gw, 1)}`, r.c, "box"]));
  qel.append(lg);
}

function disclosures(d) {
  const rows = d.disclosures;
  $("r-disc-wrap").hidden = !rows.length;
  $("r-disc").innerHTML = rows
    .map(
      (r) =>
        `<li>${esc(r.metric)}: <strong>${esc(fmt(r.value, r.value % 1 ? 1 : 0))} ${esc(r.unit || "")}</strong> ` +
        `<span class="meta">(${esc(r.entity)}, ${esc(r.as_of)}; ${esc(r.metric_type)}) · <a href="${esc(r.source_url)}" rel="noopener" target="_blank">source</a></span></li>`
    )
    .join("");
}

async function renderRegion(slug) {
  const d = await series(slug);
  const t = tokens();
  const s = d.summary;
  $("r-name").textContent = d.name;
  const win = `${s.from_year} to the 12 months ending ${monthName(s.last12_to)}`;
  $("r-sub").textContent = `${GROUP_LABEL[d.group]} · growth measured from ${win}.`;
  $("r-tiles").innerHTML = tiles(s);
  $("r-notes").innerHTML = d.notes.map((n) => `<div class="note">${esc(n)}</div>`).join("");
  indexChart(t, d);
  mwChart(t, d);
  bandChart(t, d);
  shapeTable(d);
  supplyChart(t, d);
  disclosures(d);
  document.title = `${d.short} · Grid Load Explorer`;
}

// ---------------------------------------------------------------- routing
function go(slug) {
  location.hash = slug ? `#/${slug}` : "#/";
}

async function route() {
  const slug = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
  const known = state.index.regions.find((r) => r.slug === slug);
  state.view = known ? slug : null;
  $("region").value = state.view || "";
  $("overview").hidden = !!state.view;
  $("region-view").hidden = !state.view;
  try {
    if (state.view) await renderRegion(state.view);
    else {
      renderOverview();
      document.title = "Grid Load Explorer";
    }
  } catch (e) {
    showError(e);
  }
}

function rerender() {
  if (!state.index) return;
  if (state.view) renderRegion(state.view).catch(showError);
  else renderOverview();
}

function showError(e) {
  console.error(e);
  const el = $("error");
  el.hidden = false;
  el.textContent =
    location.protocol === "file:"
      ? "The explorer reads its data with fetch(), which browsers block on file:// pages. Serve the folder instead, e.g. `python -m http.server` inside app/."
      : `Could not load the data (${e.message}).`;
}

function buildMenu() {
  const sel = $("region");
  const opt = (v, label) => Object.assign(document.createElement("option"), { value: v, textContent: label });
  sel.append(opt("", "Overview: all regions"));
  for (const [g, label] of GROUP_MENU) {
    const og = document.createElement("optgroup");
    og.label = label;
    state.index.regions.filter((r) => r.group === g).forEach((r) => og.append(opt(r.slug, `${r.name} (${pct(r.growth_pct)})`)));
    sel.append(og);
  }
  sel.addEventListener("change", () => go(sel.value));
}

async function init() {
  try {
    state.index = await getJSON("data/index.json");
  } catch (e) {
    showError(e);
    return;
  }
  const c = new Date(state.index.cutoff + "T00:00:00Z");
  $("cutoff").textContent = `Data through ${c.toLocaleDateString("en-US", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })}.`;
  buildMenu();
  document.querySelectorAll(".seg button").forEach((b) =>
    b.addEventListener("click", () => {
      state.band = b.dataset.band;
      document.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      renderOverview();
    })
  );
  window.addEventListener("hashchange", route);
  let lastW = window.innerWidth;
  let timer;
  window.addEventListener("resize", () => {
    if (Math.abs(window.innerWidth - lastW) < 8) return; // ignore mobile URL-bar height changes
    lastW = window.innerWidth;
    clearTimeout(timer);
    timer = setTimeout(rerender, 150);
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", rerender);
  await route();
}

init();
