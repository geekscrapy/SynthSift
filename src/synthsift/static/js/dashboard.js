/* Dashboard page: the corpus at a glance.
 *
 * - key figures (conversations, events, tool and sub-agent calls, list hits, findings, terms, concepts);
 * - activity over time: one column chart per measure (events, IOC & keyword hits, tool calls,
 *   sub-agent calls) on a shared time axis, with a selectable slice size and a table view;
 * - the long tail: the rarest (or most common) terms, concepts, list hits and tools, each with
 *   a strip showing when it occurs.
 *
 * Everything is a link to the Graph, Nodes or Timeline page with the matching filter (see the URL
 * parameters those pages accept). Dragging across the charts narrows the shared time window.
 * The conversation scope and time window are shared with the other pages.
 */
"use strict";

(() => {
  const { store, el, icon, fmt, plural, fmtTime, pageURL, debounce } = SS;
  const W = WS.W;
  const $ = (id) => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";

  const DAY = 864e5;
  const SLICES = [
    ["auto", "Auto", 0], ["1m", "1 minute", 6e4], ["5m", "5 minutes", 3e5], ["15m", "15 minutes", 9e5], ["30m", "30 minutes", 18e5],
    ["1h", "1 hour", 36e5], ["3h", "3 hours", 108e5], ["6h", "6 hours", 216e5], ["12h", "12 hours", 432e5],
    ["1d", "1 day", DAY], ["1w", "1 week", 7 * DAY], ["30d", "30 days", 30 * DAY],
  ];
  const AUTO_SLICES = 60; // "auto" picks the smallest slice giving at most this many bars
  const MAX_SLICES = 500;
  const RANGES = [["all", "All", 0], ["1d", "Last day", DAY], ["7d", "Last 7 days", 7 * DAY], ["30d", "Last 30 days", 30 * DAY]];
  const HIT_CATS = ["ioc", "keyword", "watchlist"];

  const P = {
    slice: store.get("dash.slice", "auto"),
    tail: store.get("dash.tail", "rare"), // rare | common
    rows: store.get("dash.rows", 10),
    table: store.get("dash.table", false),
    scale: store.get("dash.scale", "linear"), // linear | log (quiet slices stay visible next to a busy one)
    q: "",
  };
  const save = () => { for (const k of ["slice", "tail", "rows", "table", "scale"]) store.set("dash." + k, P[k]); };

  /* ================================================================ data */
  const tsOf = (e) => { const t = e && e.ts ? Date.parse(e.ts) : NaN; return isNaN(t) ? null : t; };
  const iso = (t) => new Date(t).toISOString();
  /** the current time window as link parameters */
  const winParams = () => ({ from: W.filter.from || undefined, to: W.filter.to || undefined });
  const minOf = (xs) => xs.reduce((a, x) => Math.min(a, x), Infinity); // no spread: lists can be long
  const reEscape = (s) => s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
  /** a Nodes-page search matching the calls of these tools (their nodes are labelled "<tool> #<n>") */
  const toolQuery = (names) => `/^(${[...names].map(reEscape).join("|")}) #\\d+$/`;

  function sliceFor(span) {
    const pick = SLICES.find((s) => s[0] === P.slice) || SLICES[0];
    const sized = SLICES.slice(1);
    if (!pick[2]) return { size: (sized.find((s) => span / s[2] <= AUTO_SLICES) || sized[sized.length - 1])[2], label: "", note: "" };
    if (span / pick[2] <= MAX_SLICES) return { size: pick[2], label: pick[1], note: "" };
    const s = sized.find((x) => span / x[2] <= MAX_SLICES) || sized[sized.length - 1];
    return { size: s[2], label: s[1], note: `${pick[1]} slices would make ${fmt(Math.ceil(span / pick[2]))} bars – showing ${s[1].toLowerCase()} slices` };
  }

  /** bins start on round times: local midnight (Mondays for week-sized slices) */
  function origin(lo, size) {
    const d = new Date(lo);
    d.setHours(0, 0, 0, 0);
    if (size >= 7 * DAY) d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    return d.getTime();
  }

  function compute() {
    const D = { events: [], timed: [], bins: [], size: 0, note: "", lo: 0, hi: 0 };
    if (!W.data) return D;
    D.events = W.data.events.filter((e) => WS.eventVisible(e.id));
    D.timed = D.events.filter((e) => tsOf(e) !== null);
    D.hits = W.findings.filter((f) => HIT_CATS.includes(f.category) && WS.eventVisible(f.event));
    D.findings = W.findings.filter((f) => WS.eventVisible(f.event));
    D.subTools = new Set(D.events.filter((e) => e.sub).map((e) => e.tool));
    D.convs = new Set(D.events.map((e) => e.c));
    if (!D.timed.length) return D;
    let lo = W.filter.from ? Date.parse(W.filter.from) : Infinity;
    let hi = W.filter.to ? Date.parse(W.filter.to) : -Infinity;
    for (const e of D.timed) {
      const t = tsOf(e);
      if (!W.filter.from) lo = Math.min(lo, t);
      if (!W.filter.to) hi = Math.max(hi, t + 1);
    }
    const { size, label, note } = sliceFor(Math.max(1, hi - lo));
    const o = origin(lo, size);
    const first = Math.floor((lo - o) / size), last = Math.ceil((hi - o) / size);
    Object.assign(D, { size, sliceLabel: label, note, lo, hi, origin: o, first });
    for (let i = first; i < last; i++) D.bins.push({ t0: o + i * size, t1: o + (i + 1) * size, events: 0, ioc: 0, keyword: 0, watchlist: 0, tools: 0, sub: 0 });
    for (const e of D.timed) {
      const b = D.bins[binIndex(D, tsOf(e))];
      if (!b) continue;
      b.events++;
      if (e.type === "tool_call") b.tools++;
      if (e.sub) b.sub++;
    }
    for (const f of D.hits) {
      const b = D.bins[binIndex(D, tsOf(W.events.get(f.event)))];
      if (b) b[f.category]++;
    }
    return D;
  }
  const binIndex = (D, t) => (t === null ? -1 : Math.floor((t - D.origin) / D.size) - D.first);
  const binWin = (b) => ({ from: iso(b.t0), to: iso(b.t1) });

  /** strip of counts over the chart's time axis, for the long-tail rows */
  function stripCounts(D, times) {
    const n = Math.min(D.bins.length, 48);
    const out = new Array(n).fill(0);
    if (!n) return out;
    const span = D.bins[D.bins.length - 1].t1 - D.bins[0].t0;
    for (const t of times) if (t !== null) out[Math.min(n - 1, Math.max(0, Math.floor(((t - D.bins[0].t0) / span) * n)))]++;
    return out;
  }

  /* ========================================================== key figures */
  function tiles(D) {
    const convs = [...D.convs].map((c) => W.convs.get(c)).filter(Boolean);
    const count = (k) => new Set(convs.map((c) => c[k])).size;
    const tools = D.events.filter((e) => e.type === "tool_call");
    const subs = tools.filter((e) => e.sub);
    const subSessions = convs.filter((c) => c.meta && c.meta.subagent).length;
    const byCat = (cats) => D.hits.filter((f) => cats.includes(f.category));
    const ioc = byCat(["ioc"]), kw = byCat(["keyword", "watchlist"]);
    const severe = D.findings.filter((f) => f.severity === "high" || f.severity === "critical").length;
    const terms = termStats(D);
    const plain = terms.filter((t) => t.n.category !== "concept"), concepts = terms.filter((t) => t.n.category === "concept");
    const entityKinds = [...new Set(plain.map((t) => t.n.category))];
    const w = winParams();
    const tile = (label, value, sub, href, title) => el("a", { class: "kpi", href, title }, el("span", { class: "kpi-label" }, label),
      el("span", { class: "kpi-value" }, fmt(value)), el("span", { class: "kpi-sub" }, sub));
    return el("div", { class: "kpi-row" },
      tile("Conversations", convs.length, `${plural(count("host"), "host")} · ${plural(count("user"), "user")} · ${plural(count("harness"), "agent")}`,
        pageURL("/", w), "Open the graph"),
      tile("Events", D.events.length, D.timed.length < D.events.length ? `${fmt(D.events.length - D.timed.length)} without a time` : "every turn in scope",
        pageURL("/", w), "Open the graph"),
      tile("Tool calls", tools.length, plural(new Set(tools.map((e) => e.tool)).size, "tool"), pageURL("/nodes", { ...w, kinds: "tool_call" }), "List the tool calls"),
      tile("Sub-agent calls", subs.length, plural(subSessions, "sub-agent session"),
        pageURL("/nodes", { ...w, kinds: "tool_call", q: toolQuery(D.subTools.size ? D.subTools : ["Task", "Agent", "sessions_spawn"]) }), "List the sub-agent calls"),
      tile("IOC hits", ioc.length, plural(new Set(ioc.map((f) => f.value.toLowerCase())).size, "indicator"),
        pageURL("/timeline", { ...w, kinds: "finding", cats: "ioc" }), "IOC list hits on the timeline"),
      tile("Keyword hits", kw.length, plural(new Set(kw.map((f) => f.value.toLowerCase())).size, "keyword"),
        pageURL("/timeline", { ...w, kinds: "finding", cats: "keyword,watchlist" }), "Keyword list and watchlist hits on the timeline"),
      tile("Findings", D.findings.length, `${fmt(severe)} high or critical`, pageURL("/timeline", { ...w, kinds: "finding" }), "Every finding on the timeline"),
      tile("Terms", plain.length, `${fmt(plain.filter((t) => t.count === 1).length)} seen once`,
        pageURL("/nodes", { ...w, kinds: entityKinds.length ? entityKinds : "none" }), "List the terms"),
      tile("Concepts", concepts.length, `${fmt(concepts.filter((t) => t.count === 1).length)} seen once`,
        pageURL("/nodes", { ...w, kinds: "concept" }), "List the concepts"));
  }

  /* ================================================================ charts */
  const CHARTS = [
    { key: "events", title: "Events", help: "Every turn: messages, thoughts, tool calls and results",
      series: [{ key: "events", label: "Events" }],
      href: (b) => pageURL("/", binWin(b)), target: "Open the graph for this slice" },
    { key: "hits", title: "IOC & keyword hits", help: "IOC list, keyword list and watchlist matches",
      series: [{ key: "ioc", label: "IOC lists" }, { key: "keyword", label: "Keyword lists" }, { key: "watchlist", label: "Watchlist" }],
      href: (b, s) => pageURL("/timeline", { ...binWin(b), kinds: "finding", cats: s || HIT_CATS }), target: "List these hits on the timeline",
      empty: () => el("span", {}, "No list hits. Add IOC and keyword lists under ", el("a", { href: "/settings#modules" }, "Settings → Modules"), ".") },
    { key: "tools", title: "Tool calls", help: "Calls of any tool",
      series: [{ key: "tools", label: "Tool calls" }],
      href: (b) => pageURL("/nodes", { ...binWin(b), kinds: "tool_call" }), target: "List these tool calls" },
    { key: "sub", title: "Sub-agent calls", help: "Tool calls that start a sub-agent (Claude Code Task / Agent, OpenClaw sessions_spawn)",
      series: [{ key: "sub", label: "Sub-agent calls" }],
      href: (b, s, D) => pageURL("/nodes", { ...binWin(b), kinds: "tool_call", q: toolQuery(D.subTools.size ? D.subTools : ["Task", "Agent", "sessions_spawn"]) }),
      target: "List these sub-agent calls" },
  ];
  const SERIES_VAR = ["--viz-1", "--viz-2", "--viz-3"];

  function niceMax(v) {
    if (v <= 1) return 1;
    const p = 10 ** Math.floor(Math.log10(v));
    return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
  }
  /** x-axis labels that don't collide: day starts (as dates) first, then times where they fit */
  function axisLabels(D, xOf, left, right) {
    const day = (t) => new Date(t).toLocaleDateString([], { month: "short", day: "numeric" });
    const cands = D.bins.map((b, i) => {
      const d = new Date(b.t0);
      if (D.size >= 30 * DAY) return { i, text: d.toLocaleDateString([], { month: "short", year: "numeric" }), rank: 0 };
      if (D.size >= DAY) return { i, text: day(b.t0), rank: 0 };
      const midnight = d.getHours() === 0 && d.getMinutes() === 0;
      return { i, text: midnight ? day(b.t0) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), rank: midnight ? 0 : 1 };
    });
    if (D.size < DAY && cands.length && cands[0].rank) cands[0] = { ...cands[0], text: `${day(D.bins[0].t0)} ${cands[0].text}`, rank: 0 };
    const placed = [];
    for (const c of [...cands].sort((a, b) => a.rank - b.rank || a.i - b.i)) {
      const w = c.text.length * 6.4, x = xOf(c.i);
      const lo = Math.max(left, x - w / 2), hi = lo + w;
      if (hi > right + 4 || placed.some((p) => lo < p.hi + 12 && p.lo < hi + 12)) continue;
      placed.push({ ...c, lo, hi });
    }
    return placed;
  }
  const svg = (tag, attrs = {}) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
    return e;
  };
  /** a column rounded (4px) at its data end, square at the baseline */
  function colPath(x, y, w, h, round) {
    const r = round ? Math.min(4, w / 2, h) : 0;
    return `M${x},${y + h}V${y + r}${r ? `Q${x},${y} ${x + r},${y}` : ""}H${x + w - r}${r ? `Q${x + w},${y} ${x + w},${y + r}` : ""}V${y + h}Z`;
  }

  const hover = { charts: [], idx: -1 };
  function renderCharts(D, box) {
    hover.charts = [];
    const width = Math.max(320, box.clientWidth - 2);
    const M = { l: 48, r: 12, t: 8 };
    const PH = 92, XA = 24; // plot height, x-axis band (last chart only)
    const plotW = width - M.l - M.r;
    const n = D.bins.length;
    const band = plotW / n;
    const barW = Math.max(1, Math.min(24, band - 2)); // <= 24px, 2px surface gap between neighbours
    CHARTS.forEach((c, ci) => {
      const lastChart = ci === CHARTS.length - 1;
      const H = M.t + PH + (lastChart ? XA : 6);
      const total = D.bins.reduce((a, b) => a + c.series.reduce((s, x) => s + b[x.key], 0), 0);
      const peak = D.bins.reduce((m, b) => Math.max(m, c.series.reduce((s, x) => s + b[x.key], 0)), 0);
      const log = P.scale === "log";
      const max = log ? 10 ** Math.max(1, Math.ceil(Math.log10(Math.max(1, peak)))) : niceMax(peak);
      const frac = (v) => (log ? Math.log10(1 + v) / Math.log10(1 + max) : v / max);
      const y = (v) => M.t + PH - frac(v) * PH;
      const ticks = log ? [0, ...[1, 10, 100, 1e3, 1e4, 1e5, 1e6].filter((t) => t <= max)] : max % 2 ? [0, max] : [0, max / 2, max];
      const root = svg("svg", { width, height: H, class: "chart-svg", role: "img", "aria-label": `${c.title} per time slice` });
      const gGrid = svg("g", { class: "grid" });
      let lastY = Infinity;
      for (const v of ticks) { // counts: whole-number ticks only, never closer than 12px
        if (v && lastY - y(v) < 12) continue;
        lastY = y(v);
        gGrid.append(svg("line", { x1: M.l, x2: width - M.r, y1: y(v) + 0.5, y2: y(v) + 0.5, class: v ? "gridline" : "baseline" }));
        const lab = svg("text", { x: M.l - 8, y: y(v) + 4, class: "tick", "text-anchor": "end" });
        lab.textContent = fmt(v);
        gGrid.append(lab);
      }
      root.append(gGrid);
      const hl = svg("rect", { class: "slot-hl", x: -100, y: M.t, width: band, height: PH });
      root.append(hl);
      const gBars = svg("g", { class: "bars" });
      D.bins.forEach((b, i) => {
        const x0 = M.l + i * band, x = x0 + (band - barW) / 2;
        const slot = svg("a", { href: c.href(b, null, D), class: "slot", "data-i": i, "aria-label": `${c.title}: ${fmt(c.series.reduce((s, x2) => s + b[x2.key], 0))}, ${SS.fmtRange(iso(b.t0), iso(b.t1))}` });
        slot.append(svg("rect", { x: x0, y: M.t, width: band, height: PH, class: "hit" }));
        gBars.append(slot);
        let cum = 0;
        const segs = c.series.map((s, si) => ({ s, si, v: b[s.key] })).filter((x2) => x2.v > 0);
        segs.forEach((seg, k) => {
          const top = k === segs.length - 1;
          const y0 = y(cum), y1 = y(cum + seg.v);
          cum += seg.v;
          const gap = top ? 0 : Math.min(2, (y0 - y1) / 2); // 2px surface gap between stacked segments
          const h = Math.max(2, y0 - y1 - gap); // a single event stays visible
          const path = svg("path", { d: colPath(x, y0 - h, barW, h, top), style: `fill: var(${SERIES_VAR[seg.si]})` });
          if (c.series.length > 1) {
            const a = svg("a", { href: c.href(b, seg.s.key, D), class: "seg", "data-i": i, "aria-label": `${seg.s.label}: ${fmt(seg.v)}` });
            a.append(path);
            gBars.append(a);
          } else slot.append(path);
        });
      });
      root.append(gBars);
      if (lastChart) {
        const gX = svg("g", { class: "xaxis" });
        for (const l of axisLabels(D, (i) => M.l + i * band + band / 2, M.l, width - M.r)) {
          const t = svg("text", { x: (l.lo + l.hi) / 2, y: M.t + PH + 16, class: "tick", "text-anchor": "middle" });
          t.textContent = l.text;
          gX.append(t);
        }
        root.append(gX);
      }
      const brush = svg("rect", { class: "brush", x: 0, y: M.t, width: 0, height: PH });
      root.append(brush);
      hover.charts.push({ hl, brush, band, M });
      const legend = c.series.length > 1 ? el("div", { class: "legend-row" }, c.series.map((s, si) =>
        el("span", { class: "key" }, el("span", { class: "swatch", style: { background: `var(${SERIES_VAR[si]})` } }), s.label))) : null;
      const fig = el("figure", { class: "chart" },
        el("figcaption", { class: "chart-head" }, el("b", {}, c.title), el("span", { class: "muted" }, c.help), el("span", { class: "grow" }),
          el("span", { class: "chart-total" }, fmt(total))),
        legend, root,
        total === 0 ? el("div", { class: "chart-empty" }, c.empty ? c.empty() : `No ${c.title.toLowerCase()} in this time range.`) : null);
      wireChart(root, D, M, band);
      box.append(fig);
    });
  }

  /* hover (one readout for every chart), click-through and drag-to-zoom */
  function showSlot(D, i, evt) {
    hover.idx = i;
    for (const c of hover.charts) c.hl.setAttribute("x", i < 0 ? -1000 : c.M.l + i * c.band);
    const tip = $("tip");
    if (i < 0 || !D.bins[i]) { tip.classList.remove("show"); return; }
    const b = D.bins[i];
    const row = (value, label, color) => el("div", { class: "tip-row" }, el("span", { class: "line-key", style: { background: color } }),
      el("b", {}, fmt(value)), el("span", {}, label));
    tip.replaceChildren(el("div", { class: "tip-head" }, SS.fmtRange(iso(b.t0), iso(b.t1))),
      row(b.events, "events", "var(--viz-1)"),
      row(b.ioc, "IOC list hits", "var(--viz-1)"), row(b.keyword, "keyword list hits", "var(--viz-2)"), row(b.watchlist, "watchlist hits", "var(--viz-3)"),
      row(b.tools, "tool calls", "var(--viz-1)"), row(b.sub, "sub-agent calls", "var(--viz-1)"),
      el("div", { class: "tip-foot" }, "Click to open · drag to zoom"));
    const r = evt && evt.clientX !== undefined ? { x: evt.clientX, y: evt.clientY } : (() => { const q = evt.target.getBoundingClientRect(); return { x: q.right, y: q.top }; })();
    tip.classList.add("show");
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = Math.max(8, Math.min(window.innerWidth - tw - 8, r.x + 14)) + "px";
    tip.style.top = Math.max(8, Math.min(window.innerHeight - th - 8, r.y + 14)) + "px";
  }
  function wireChart(root, D, M, band) {
    const idxAt = (evt) => {
      const r = root.getBoundingClientRect();
      return Math.max(0, Math.min(D.bins.length - 1, Math.floor((evt.clientX - r.left - M.l) / band)));
    };
    let drag = null, dragged = false;
    root.addEventListener("pointermove", (e) => {
      if (drag !== null) {
        const i = idxAt(e);
        const a = Math.min(drag, i), b = Math.max(drag, i);
        if (i !== drag && !dragged) { dragged = true; root.setPointerCapture(e.pointerId); tipOff(); }
        if (!dragged) return;
        for (const c of hover.charts) { c.brush.setAttribute("x", c.M.l + a * c.band); c.brush.setAttribute("width", (b - a + 1) * c.band); }
        return;
      }
      showSlot(D, e.target.closest && e.target.closest("[data-i]") ? idxAt(e) : -1, e);
    });
    root.addEventListener("pointerleave", () => { if (drag === null) showSlot(D, -1); });
    root.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      drag = idxAt(e); dragged = false;
    });
    root.addEventListener("dragstart", (e) => e.preventDefault()); // links would otherwise be dragged
    root.addEventListener("pointerup", (e) => {
      if (drag === null) return;
      const i = idxAt(e), a = Math.min(drag, i), b = Math.max(drag, i);
      drag = null;
      for (const c of hover.charts) c.brush.setAttribute("width", 0);
      if (dragged) WS.setFilter({ from: iso(D.bins[a].t0), to: iso(D.bins[b].t1) });
    });
    const tipOff = () => $("tip").classList.remove("show");
    root.addEventListener("click", (e) => { if (dragged) { e.preventDefault(); dragged = false; } }, true);
    root.addEventListener("focusin", (e) => { const s = e.target.closest("[data-i]"); if (s) showSlot(D, Number(s.dataset.i), e); });
    root.addEventListener("focusout", () => showSlot(D, -1));
  }

  /** the charts' table twin: one row per slice with any activity */
  function chartTable(D) {
    const cols = [["events", "Events", (b) => CHARTS[0].href(b)], ["ioc", "IOC", (b) => CHARTS[1].href(b, "ioc")],
      ["keyword", "Keyword", (b) => CHARTS[1].href(b, "keyword")], ["watchlist", "Watchlist", (b) => CHARTS[1].href(b, "watchlist")],
      ["tools", "Tool calls", (b) => CHARTS[2].href(b)], ["sub", "Sub-agent calls", (b) => CHARTS[3].href(b, null, D)]];
    const rows = D.bins.filter((b) => cols.some(([k]) => b[k]));
    return el("div", { class: "chart-table" }, el("table", { class: "data-grid compact" },
      el("thead", {}, el("tr", {}, el("th", {}, "Slice"), cols.map(([, label]) => el("th", { class: "num" }, label)))),
      el("tbody", {}, rows.map((b) => el("tr", {}, el("td", {}, SS.fmtRange(iso(b.t0), iso(b.t1))),
        cols.map(([k, , href]) => el("td", { class: "num" }, b[k] ? el("a", { href: href(b) }, fmt(b[k])) : "")))))));
  }

  /* ============================================================ long tail */
  let termCache = null;
  /** entities with their mentions inside the scope and time window */
  function termStats(D) {
    if (termCache && termCache.D === D) return termCache.rows;
    const rows = [];
    for (const n of W.nodes.values()) {
      if (n.type !== "entity") continue;
      const occ = (n.occ || []).filter(([pid]) => WS.paraVisible(pid));
      if (!occ.length) continue;
      const evs = occ.map(([pid]) => W.events.get(W.paras.get(pid).e));
      const times = evs.map(tsOf);
      const known = times.filter((t) => t !== null);
      rows.push({ n, count: occ.length, convs: new Set(evs.map((e) => e.c)).size, first: known.length ? minOf(known) : null, times });
    }
    termCache = { D, rows };
    return rows;
  }
  function hitStats(D) {
    const by = new Map();
    for (const f of D.hits) {
      const key = `${f.category}\u0000${f.value.toLowerCase()}`;
      const ev = W.events.get(f.event);
      if (!by.has(key)) by.set(key, { f, count: 0, convs: new Set(), times: [] });
      const r = by.get(key);
      r.count++; r.convs.add(f.conv); r.times.push(tsOf(ev));
    }
    return [...by.values()].map((r) => ({ ...r, convs: r.convs.size, first: minOf(r.times.filter((t) => t !== null)) }));
  }
  function toolStats(D) {
    const by = new Map();
    for (const e of D.events) {
      if (e.type !== "tool_call") continue;
      if (!by.has(e.tool)) by.set(e.tool, { tool: e.tool, sub: !!e.sub, count: 0, convs: new Set(), times: [] });
      const r = by.get(e.tool);
      r.count++; r.convs.add(e.c); r.times.push(tsOf(e));
    }
    return [...by.values()].map((r) => ({ ...r, convs: r.convs.size, first: minOf(r.times.filter((t) => t !== null)) }));
  }

  function strip(D, times) {
    const counts = stripCounts(D, times);
    const w = 120, h = 20, n = counts.length;
    const root = svg("svg", { width: w, height: h, class: "strip", "aria-hidden": "true" });
    if (!n) return root;
    const max = Math.max(1, ...counts), bw = w / n;
    root.append(svg("line", { x1: 0, x2: w, y1: h - 0.5, y2: h - 0.5, class: "baseline" }));
    counts.forEach((c, i) => {
      if (!c) return;
      const bh = Math.max(2, (c / max) * (h - 2));
      root.append(svg("rect", { x: i * bw + (bw > 3 ? 0.5 : 0), y: h - bh, width: Math.max(1, bw - (bw > 3 ? 1 : 0)), height: bh }));
    });
    return root;
  }

  function tailCard(D, { title, icon: ic, items, name, sub, href, alt, empty }) {
    const rx = SS.makeRegex(P.q.trim());
    const shown = items.filter((r) => !rx || rx.test(name(r)));
    const rare = P.tail === "rare";
    shown.sort((a, b) => (rare ? a.count - b.count : b.count - a.count) || ((b.first || 0) - (a.first || 0)) || name(a).localeCompare(name(b)));
    const once = items.filter((r) => r.count === 1).length;
    const rows = shown.slice(0, P.rows);
    return el("section", { class: "tail-card" },
      el("header", {}, icon(ic, "sm"), el("h3", {}, `${rare ? "Rarest" : "Most common"} ${title}`), el("span", { class: "grow" }),
        el("span", { class: "muted" }, `${fmt(items.length)} · ${fmt(once)} seen once`)),
      rows.length ? el("table", { class: "tail" },
        el("thead", {}, el("tr", {}, el("th", {}, "Name"), el("th", { class: "num" }, "Count"), el("th", { class: "num" }, "Conv."),
          el("th", {}, "First seen"), el("th", {}, "When"))),
        el("tbody", {}, rows.map((r) => el("tr", {},
          el("td", { class: "name" }, el("a", { href: href(r), title: name(r) }, name(r)), sub ? sub(r) : null,
            alt ? el("a", { class: "alt", href: alt.href(r), title: alt.title }, icon(alt.icon, "xs")) : null),
          el("td", { class: "num" }, fmt(r.count)), el("td", { class: "num" }, fmt(r.convs)),
          el("td", { class: "cell-muted" }, r.first && isFinite(r.first) ? fmtTime(iso(r.first)) : "–"),
          el("td", {}, strip(D, r.times)))))) : el("div", { class: "tail-empty muted" }, rx ? "Nothing matches the filter." : empty));
  }

  function tails(D) {
    const w = winParams();
    const terms = termStats(D);
    const kindTag = (r) => { const k = WS.kindOf(r.n); return el("span", { class: "tag kind", style: { "--c": k.color } }, el("span", { class: "dot" }), k.label); };
    const nodeLinks = {
      href: (r) => pageURL("/", { ...w, select: r.n.id }),
      alt: { icon: "table_rows", title: "Open on the Nodes page", href: (r) => pageURL("/nodes", { ...w, select: r.n.id }) },
    };
    return el("div", { class: "tail-grid" },
      tailCard(D, { title: "terms", icon: "label", items: terms.filter((r) => r.n.category !== "concept"), name: (r) => r.n.label, sub: kindTag, ...nodeLinks,
        empty: "No terms in this scope." }),
      tailCard(D, { title: "concepts", icon: "lightbulb", items: terms.filter((r) => r.n.category === "concept"), name: (r) => r.n.label, ...nodeLinks,
        empty: "No concepts in this scope." }),
      tailCard(D, { title: "IOC & keyword hits", icon: "playlist_add_check", items: hitStats(D), name: (r) => r.f.value,
        sub: (r) => el("span", { class: "tag" }, r.f.label.replace(/^(List hit|Watchlist): /, "")),
        href: (r) => pageURL("/timeline", { ...w, kinds: "finding", cats: r.f.category, q: r.f.value }),
        empty: el("span", {}, "No list hits. Add lists under ", el("a", { href: "/settings#modules" }, "Settings → Modules"), ".") }),
      tailCard(D, { title: "tools", icon: "build", items: toolStats(D), name: (r) => r.tool,
        sub: (r) => (r.sub ? el("span", { class: "tag" }, "sub-agent") : null),
        href: (r) => pageURL("/nodes", { ...w, kinds: "tool_call", q: toolQuery([r.tool]) }),
        empty: "No tool calls in this scope." }));
  }

  /* ============================================================== render */
  function renderToolbar(D) {
    const latest = D.timed.length ? D.timed.reduce((a, e) => Math.max(a, tsOf(e)), -Infinity) : null;
    const range = RANGES.find(([, , span]) => span && W.filter.from && !W.filter.to && latest && Math.abs(latest + 1 - span - Date.parse(W.filter.from)) < 1000);
    const active = range ? range[0] : W.filter.from || W.filter.to ? "" : "all";
    const seg = (items, cur, onPick, label) => el("div", { class: "segmented sm", role: "group", "aria-label": label },
      items.map(([k, text, title]) => el("button", { class: k === cur ? "on" : "", title, onclick: () => onPick(k) }, text)));
    $("toolbar").replaceChildren(...[
      seg(RANGES.map(([k, t, span]) => [k, t, span ? `${t} up to the latest turn in scope` : "The whole time range"]), active, (k) => {
        const span = RANGES.find((r) => r[0] === k)[2];
        WS.setFilter(span && latest ? { from: iso(latest + 1 - span), to: "" } : { from: "", to: "" });
      }, "Time range (of the data)"),
      seg([["linear", "Linear"], ["log", "Log"]], P.scale, (k) => { P.scale = k; save(); render(); }, "Chart scale"),
      el("label", { class: "field-row" }, el("span", { class: "muted" }, "Slice"),
        el("select", { class: "select sm", "aria-label": "Time slice", onchange: (e) => { P.slice = e.target.value; save(); render(); } },
          SLICES.map(([k, t]) => el("option", { value: k, selected: k === P.slice }, k === "auto" && D.size ? `Auto (${sliceName(D.size)})` : t)))),
      D.note ? el("span", { class: "muted note" }, D.note) : null,
      el("span", { class: "grow" }),
      el("span", { class: "muted" }, "Long tail"),
      seg([["rare", "Rarest"], ["common", "Most common"]], P.tail, (k) => { P.tail = k; save(); render(); }, "Long tail order"),
      el("select", { class: "select sm", "aria-label": "Rows per list", onchange: (e) => { P.rows = Number(e.target.value); save(); render(); } },
        [10, 25, 50, 100].map((v) => el("option", { value: v, selected: v === P.rows }, `${v} rows`)))].filter(Boolean));
  }
  const sliceName = (size) => (SLICES.find((s) => s[2] === size) || [0, `${fmt(size / 6e4)} min`])[1];

  function render() {
    WS.renderScope();
    $("reset").classList.toggle("hidden", !WS.scopeActive());
    const body = $("body");
    if (!W.data) { $("toolbar").replaceChildren(); body.replaceChildren(el("div", { class: "grid-empty" }, icon("dashboard"), "Load transcripts on the Graph page first.")); return; }
    const D = compute();
    renderToolbar(D);
    const timeHead = el("div", { class: "section-head" }, el("h2", {}, "Over time"),
      el("span", { class: "muted" }, D.bins.length ? `${plural(D.bins.length, "slice")} of ${sliceName(D.size).toLowerCase()} · ${SS.fmtRange(iso(D.lo), iso(D.hi))}` : ""),
      el("span", { class: "grow" }),
      D.bins.length ? el("button", { class: "btn text sm", onclick: () => { P.table = !P.table; save(); render(); } },
        icon(P.table ? "bar_chart" : "table", "sm"), P.table ? "Show charts" : "Show as table") : null);
    const charts = el("div", { class: "charts" });
    body.replaceChildren(tiles(D), timeHead, charts, el("div", { class: "section-head" }, el("h2", {}, "Long tail"),
      el("span", { class: "muted" }, P.tail === "rare" ? "The rarest items first – where the unusual usually hides" : "The most common items first")), tails(D));
    if (!D.bins.length) charts.append(el("div", { class: "chart-empty" }, "No turns with a timestamp in this scope."));
    else if (P.table) charts.append(chartTable(D));
    else renderCharts(D, charts);
    const n = P.q.trim() ? document.querySelectorAll(".tail tbody tr").length : 0;
    $("q-result").textContent = P.q.trim() ? `${fmt(n)} shown` : "";
  }

  /* ================================================================ boot */
  function buildRail() {
    $("rail").replaceChildren(WS.scopeSection(() => WS.resetScope()),
      el("div", { class: "rail-section" }, el("h3", {}, icon("info", "xs"), "About"),
        el("p", { class: "rail-note" }, "Every number, bar and row opens the matching Graph, Nodes or Timeline view. Drag across the charts to narrow the time window; the scope and time window apply to every page.")));
  }

  async function boot() {
    WS.wireTopbar("dash.railClosed");
    buildRail();
    $("q").addEventListener("input", debounce(() => { P.q = $("q").value; render(); }, 150));
    try {
      await WS.load();
    } catch (e) {
      SS.snack("Could not load the workspace: " + e.message);
    }
    WS.scopeFromURL();
    history.replaceState(null, "", location.pathname);
    render();
    WS.on(() => render());
    window.addEventListener("resize", debounce(render, 150));
    WS.watchVersion();
  }
  boot();
  window.SynthSiftDashboard = { P, render, compute };
})();
