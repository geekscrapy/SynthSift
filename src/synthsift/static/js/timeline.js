/* Timeline page: every turn of every conversation in scope (user, LLM, thought,
 * tool call, tool result, system) in time order, grouped by day, with its
 * findings, tags and comment. Scrolling loads more rows; every column has a
 * filter under its header. The conversation scope, time window, tag filter and
 * "hide ignored" are shared with the other pages.
 *
 * URL parameters (the dashboard's links): the shared scope (from, to, host, …)
 * and kinds=<turn type, or "finding">, cats=<finding categories>, sev=<minimum>, q=<search>.
 */
"use strict";

(() => {
  const { store, el, icon, snack, debounce, fmt, plural } = SS;
  const W = WS.W;
  const $ = (id) => document.getElementById(id);
  const CHUNK = 200; // rows added per scroll step

  const emptyColumnFilters = () => ({ when: "", kind: "", what: "", findings: "", tags: "", where: "", agent: "", conv: "" });
  const P = {
    q: store.get("timeline.q", ""),
    cf: { ...emptyColumnFilters(), ...store.get("timeline.cf", {}) }, // column filters
    order: store.get("timeline.order", 1), // 1 = oldest first
    commentedOnly: store.get("timeline.commentedOnly", false),
    hiddenCols: new Set(store.get("timeline.hiddenCols", ["agent"])),
    checked: new Set(),
    anchor: null,
    detail: null, // row key
    rows: [],
    shown: CHUNK,
  };
  const save = () => {
    for (const k of ["q", "cf", "order", "commentedOnly"]) store.set("timeline." + k, P[k]);
    store.set("timeline.hiddenCols", [...P.hiddenCols]);
  };

  /* ---------------------------------------------------------------- rows */
  // One row per turn. What does not change with tags or filters is built once per workspace load. A turn without a
  // timestamp (e.g. a system prompt) takes the time of the next turn of its conversation that has one.
  let base = { data: null, rows: [] };
  function baseRows() {
    if (base.data === W.data) return base.rows;
    const rows = [];
    const byConv = new Map();
    for (const ev of W.data ? W.data.events : []) {
      const r = { key: "event:" + ev.id, target: "event:" + ev.id, ev, conv: ev.c, when: ev.ts || "", approx: false, text: null };
      rows.push(r);
      if (!byConv.has(ev.c)) byConv.set(ev.c, []);
      byConv.get(ev.c).push(r);
    }
    for (const list of byConv.values()) {
      let next = "";
      for (let i = list.length - 1; i >= 0; i--) {
        if (list[i].when) next = list[i].when;
        else if (next) { list[i].when = next; list[i].approx = true; }
      }
    }
    base = { data: W.data, rows };
    return rows;
  }
  const textOf = (r) => (r.text ??= r.ev.p.map((pid) => (W.paras.get(pid) || {}).t || "").join("\n"));
  const typeInfo = (type) => W.kinds.get(type) || { label: type.replace(/_/g, " "), color: "#80868b" };
  const convOf = (r) => W.convs.get(r.conv) || {};

  // tags (the turn's own and its session's), comment and findings, as they are now
  function decorate(r) {
    const own = WS.tagsFor(r.target), session = WS.tagsFor("conv:" + r.conv);
    r.tags = [...new Set([...own, ...session])];
    r.inherited = new Set(session.filter((t) => !own.includes(t)));
    r.comment = (WS.annOf(r.target) || {}).comment || "";
    r.findings = W.findingsByEvent.get(r.ev.id) || [];
    r.severity = SS.worstSeverity(r.findings);
  }

  function searchText(r) {
    const c = convOf(r);
    return [r.ev.label, textOf(r), r.comment, r.tags.join(" "), `${c.title} ${c.host} ${c.user} ${c.harness}`,
      r.findings.map((f) => `${f.label} ${f.detail} ${f.category}`).join(" ")].join("\n");
  }

  /* ------------------------------------------------------------ columns */
  function timeOnly(ts) {
    const d = new Date(ts);
    return isNaN(d) ? ts : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  const snippet = (r) => { const t = textOf(r).replace(/\s+/g, " ").trim(); return t.length > 280 ? t.slice(0, 280) + "…" : t; };
  const textFilter = (placeholder, value) => ({ placeholder, test: (q) => { const rx = SS.makeRegex(q.trim()); return rx && ((r) => rx.test(value(r))); } });
  function findingsTest(v) {
    if (v === "has") return (r) => r.findings.length > 0;
    if (v === "none") return (r) => !r.findings.length;
    if (v.startsWith("sev:")) { const min = SS.sevRank(v.slice(4)); return (r) => r.findings.length > 0 && SS.sevRank(r.severity) >= min; }
    if (v.startsWith("cat:")) { const cats = new Set(v.slice(4).split(",")); return (r) => r.findings.some((f) => cats.has(f.category)); }
    return null;
  }
  const catLabel = (key) => ((W.data && W.data.security && W.data.security.categories) || {})[key] || key;
  const opt = (value, label) => el("option", { value }, label);

  const COLS = [
    {
      key: "when", label: "Time", fixed: true,
      cell: (r) => el("td", { class: "cell-muted", title: r.approx ? `No timestamp – placed at the next turn (${r.when})` : r.when || "No timestamp" },
        r.when ? (r.approx ? "≈ " : "") + timeOnly(r.when) : "–"),
      filter: textFilter("hh:mm, date…", (r) => (r.when ? `${r.when} ${SS.fmtTime(r.when)} ${SS.fmtDay(r.when)} ${timeOnly(r.when)}` : "")),
    },
    {
      key: "kind", label: "Kind", fixed: true,
      cell: (r) => {
        const k = typeInfo(r.ev.type);
        return el("td", {}, el("span", { class: "kind-cell" }, el("span", { class: "msi xs", style: { color: k.color } }, SS.ROLE_ICON[r.ev.type] || "chat"), k.label));
      },
      filter: {
        options: (counts) => [opt("", "All kinds"), ...[...counts.kinds].sort((a, b) => b[1] - a[1]).map(([t, n]) => opt(t, `${typeInfo(t).label} (${fmt(n)})`))],
        test: (v) => (r) => r.ev.type === v,
      },
    },
    {
      key: "what", label: "What", fixed: true,
      cell: (r) => el("td", { class: "cell-what" },
        el("div", { class: "what" }, r.ev.label),
        el("div", { class: `cell-muted wrap clamp${r.ev.type === "tool_call" || r.ev.type === "tool_result" ? " mono" : ""}` }, snippet(r)),
        r.comment ? el("div", { class: "cmt" }, icon("comment", "xs"), r.comment) : null),
      filter: textFilter("Text or /regex/…", (r) => `${r.ev.label}\n${textOf(r)}\n${r.comment}`),
    },
    {
      key: "findings", label: "Findings",
      cell: (r) => el("td", { class: "cell-findings" }, r.findings.slice(0, 3).map((f) => el("div", { class: `f sev-${f.severity}`, title: `${f.label} – ${f.detail}` },
        el("span", { class: "sev-chip" }, f.severity), el("span", { class: "f-label" }, f.label))),
        r.findings.length > 3 ? el("div", { class: "cell-muted" }, `+ ${r.findings.length - 3} more`) : null),
      filter: {
        options: (counts) => {
          const cur = P.cf.findings;
          const cats = [...counts.cats].sort((a, b) => b[1] - a[1]);
          const custom = cur.startsWith("cat:") && cur.includes(",") ? [opt(cur, cur.slice(4).split(",").map(catLabel).join(" + "))] : [];
          return [opt("", "All turns"), opt("has", "With findings"), opt("none", "Without findings"),
            el("optgroup", { label: "Severity" }, ...SS.SEV_ORDER.slice(1).reverse().map((s) => opt("sev:" + s, s === "critical" ? "Critical" : `${s[0].toUpperCase() + s.slice(1)} or worse`))),
            el("optgroup", { label: "Category" }, ...custom, ...cats.map(([k, n]) => opt("cat:" + k, `${catLabel(k)} (${fmt(n)})`)))];
        },
        test: findingsTest,
      },
    },
    {
      key: "tags", label: "Tags",
      cell: (r) => el("td", {}, el("div", { class: "cell-tags", html: WS.tagChipsHTML(r.tags, r.inherited) })),
      filter: textFilter("Tag…", (r) => r.tags.join(" ")),
    },
    {
      key: "where", label: "Host / user",
      cell: (r) => { const c = convOf(r); return el("td", { class: "cell-muted" }, c.host ? `${c.host} / ${c.user}` : "–"); },
      filter: textFilter("Host or user…", (r) => { const c = convOf(r); return `${c.host} / ${c.user}`; }),
    },
    {
      key: "agent", label: "Agent",
      cell: (r) => el("td", { class: "cell-muted" }, convOf(r).harness || "–"),
      filter: textFilter("Agent…", (r) => convOf(r).harness || ""),
    },
    {
      key: "conv", label: "Conversation",
      cell: (r) => {
        const c = convOf(r);
        return el("td", {}, c.title ? el("span", { class: "conv-cell", title: c.source }, el("span", { class: "dot", style: { background: c.color } }), c.title) : "–");
      },
      filter: textFilter("Title or file…", (r) => { const c = convOf(r); return `${c.title} ${c.source}`; }),
    },
  ];
  const visibleCols = () => COLS.filter((c) => c.fixed || !P.hiddenCols.has(c.key));
  // filters of hidden columns do not apply (nothing on screen would say they are on)
  const activeColumnFilters = () => visibleCols().filter((c) => P.cf[c.key]);

  function computeRows() {
    const rx = SS.makeRegex(P.q.trim());
    const tests = activeColumnFilters().map((c) => c.filter.test(P.cf[c.key])).filter(Boolean);
    const kinds = new Map(), cats = new Map();
    const out = [];
    let inScope = 0;
    for (const r of baseRows()) {
      if (!WS.convVisible(r.conv) || !WS.inWindow(r.when)) continue;
      decorate(r);
      if (W.hideIgnored && !W.tagFilter.has("ignore") && r.tags.includes("ignore")) continue;
      if (W.tagFilter.size && !r.tags.some((t) => W.tagFilter.has(t))) continue;
      if (P.commentedOnly && !r.comment) continue;
      inScope++;
      kinds.set(r.ev.type, (kinds.get(r.ev.type) || 0) + 1);
      for (const f of r.findings) cats.set(f.category, (cats.get(f.category) || 0) + 1);
      if (rx && !rx.test(searchText(r))) continue;
      if (tests.some((t) => !t(r))) continue;
      out.push(r);
    }
    const dir = P.order;
    out.sort((a, b) => {
      if (!a.when !== !b.when) return a.when ? -1 : 1; // undated conversations last
      return (a.when.localeCompare(b.when) || a.conv.localeCompare(b.conv) || a.ev.seq - b.ev.seq) * dir;
    });
    return { rows: out, inScope, counts: { kinds, cats } };
  }

  function filtersActive() {
    return !!(P.q || activeColumnFilters().length || P.commentedOnly || WS.scopeActive());
  }
  function resetPageFilters() {
    P.q = ""; $("q").value = "";
    P.cf = emptyColumnFilters(); P.commentedOnly = false;
    save();
  }
  function resetFilters() {
    resetPageFilters();
    grid = null; // rebuilt with the emptied filter inputs
    WS.resetScope(); // re-renders
  }
  /** a dashboard link: only the page filters it names apply */
  function filtersFromURL(params) {
    if (!["kinds", "cats", "sev", "q"].some((k) => params.has(k))) return;
    resetPageFilters();
    const list = (k) => (params.get(k) || "").split(",").filter(Boolean);
    const kinds = list("kinds");
    if (params.has("cats")) P.cf.findings = "cat:" + list("cats").join(",");
    else if (params.has("sev")) P.cf.findings = "sev:" + params.get("sev");
    else if (kinds.includes("finding")) P.cf.findings = "has";
    const types = kinds.filter((k) => k !== "finding");
    if (types.length === 1) P.cf.kind = types[0];
    if (P.cf.findings) P.hiddenCols.delete("findings");
    if (params.has("q")) { P.q = params.get("q"); $("q").value = P.q; }
    save();
  }

  /* ---------------------------------------------------------------- rail */
  function buildRail() {
    $("rail").replaceChildren(
      WS.scopeSection(resetFilters),
      el("div", { class: "rail-section" },
        WS.tagsHeading(),
        el("div", { class: "chip-row", id: "tagf" }),
        el("div", { class: "chip-row", style: { marginTop: "6px" } }, el("button", { id: "commented", onclick: () => { P.commentedOnly = !P.commentedOnly; changed(); } }))),
      el("div", { class: "rail-section" },
        el("h3", {}, icon("swap_vert", "xs"), "Order"),
        el("div", { class: "segmented sm", id: "order", role: "group", "aria-label": "Order" })));
  }
  function renderRail() {
    WS.renderScope();
    WS.renderTagFilter($("tagf"));
    const cm = $("commented");
    cm.className = `chip sm${P.commentedOnly ? " selected" : ""}`;
    cm.replaceChildren(icon("comment", "xs"), "With comments only");
    $("order").replaceChildren(...[[1, "Oldest first", "arrow_downward"], [-1, "Newest first", "arrow_upward"]].map(([v, label, ic]) =>
      el("button", { class: P.order === v ? "on" : "", onclick: () => { P.order = v; changed(); } }, icon(ic, "xs"), label)));
    $("reset").classList.toggle("hidden", !filtersActive());
  }

  /* --------------------------------------------------------------- toolbar */
  const rowIndex = (key) => P.rows.findIndex((r) => r.key === key);
  const checkedTargets = () => [...P.checked];

  function renderToolbar(inScope) {
    const n = P.rows.length;
    const nf = P.rows.filter((r) => r.findings.length).length;
    const left = P.checked.size ? WS.bulkBar({ checked: P.checked, keys: P.rows.map((r) => r.key), canSelectAll: P.checked.size < n,
      targets: checkedTargets, redraw: () => render({ keepRail: true }) }) : el("span", { class: "count" },
      `${plural(n, "turn")} · ${plural(P.dayCounts.size, "day")}${nf ? ` · ${fmt(nf)} with findings` : ""}`,
      n < inScope ? el("span", { class: "muted" }, ` (of ${fmt(inScope)} in scope)`) : null);
    const clear = activeColumnFilters().length ? el("button", { class: "btn text sm", onclick: () => { P.cf = emptyColumnFilters(); grid = null; changed(); } },
      icon("filter_alt_off"), "Clear column filters") : null;
    $("toolbar").replaceChildren(...[left, el("span", { class: "grow" }), clear].filter(Boolean));
    $("q-result").textContent = P.q ? `${fmt(n)} ${n === 1 ? "turn" : "turns"}` : "";
  }

  /* ------------------------------------------------------------------ grid */
  // The header (titles and the filter row) is built once per set of columns, so typing in a filter keeps its focus;
  // the body is refilled on every change and grows as the list is scrolled.
  let grid = null;
  function buildGrid(cols) {
    const selects = new Map();
    const filterCell = (c) => {
      if (!c.filter) return el("th");
      if (c.filter.options) {
        const sel = el("select", { class: "col-filter", "aria-label": `Filter ${c.label.toLowerCase()}`, onchange: (e) => { P.cf[c.key] = e.target.value; changed(); } });
        selects.set(c.key, sel);
        return el("th", {}, sel);
      }
      const inp = el("input", { type: "search", class: "col-filter", placeholder: c.filter.placeholder, "aria-label": `Filter ${c.label.toLowerCase()}`, spellcheck: "false" });
      inp.value = P.cf[c.key];
      inp.addEventListener("input", debounce(() => { P.cf[c.key] = inp.value; changed(); }, 200));
      return el("th", {}, inp);
    };
    const orderIcon = icon("arrow_downward");
    const titles = el("tr", {}, el("th", { class: "cb" }), ...cols.map((c) => el("th", {}, c.key === "when"
      ? el("button", { title: "Reverse the order", onclick: () => { P.order = -P.order; changed(); } }, c.label, orderIcon)
      : el("button", { style: { cursor: "default" } }, c.label))));
    const filters = el("tr", { class: "filters" }, el("th", { class: "cb" }), ...cols.map(filterCell));
    const thead = el("thead", {}, titles, filters);
    const tbody = el("tbody");
    return { key: cols.map((c) => c.key).join(), cols, table: el("table", { class: "data-grid timeline-grid" }, thead, tbody), thead, tbody, titles, filters, selects, orderIcon };
  }

  function renderGrid(counts) {
    const wrap = $("grid-wrap");
    if (!W.data) {
      grid = null;
      wrap.replaceChildren(el("div", { class: "grid-empty" }, icon("timeline"), "No transcripts analysed yet. ",
        el("a", { href: "/" }, "Load or upload transcripts on the graph page"), "."));
      return;
    }
    const cols = visibleCols();
    if (!grid || grid.key !== cols.map((c) => c.key).join() || !wrap.contains(grid.table)) {
      grid = buildGrid(cols);
      wrap.replaceChildren(grid.table);
    }
    // header state: page checkbox, order arrow, filter options and which filters are on
    grid.titles.firstChild.replaceWith(WS.pageCheckbox(P.rows.map((r) => r.key), P.checked, () => render({ keepRail: true })));
    grid.orderIcon.textContent = P.order > 0 ? "arrow_downward" : "arrow_upward";
    for (const [key, sel] of grid.selects) {
      sel.replaceChildren(...COLS.find((c) => c.key === key).filter.options(counts));
      sel.value = P.cf[key];
    }
    cols.forEach((c, i) => { const f = grid.filters.children[i + 1].firstChild; if (f) f.classList.toggle("on", !!P.cf[c.key]); });
    wrap.style.setProperty("--thead-h", grid.thead.offsetHeight + "px");
    // body
    grid.tbody.replaceChildren();
    grid.lastDay = undefined;
    if (!P.rows.length) {
      grid.tbody.append(el("tr", { class: "empty" }, el("td", { colspan: cols.length + 1 }, el("div", { class: "grid-empty" }, icon("timeline"),
        filtersActive() ? "No turns match these filters. " : "No turns in scope.",
        filtersActive() ? el("button", { class: "btn text sm", onclick: resetFilters }, "Reset filters") : null))));
      return;
    }
    appendRows(0, Math.min(P.shown, P.rows.length));
  }

  function appendRows(from, to) {
    const cols = grid.cols;
    grid.tbody.querySelector("tr.more")?.remove();
    const frag = document.createDocumentFragment();
    for (const r of P.rows.slice(from, to)) {
      const d = r.when ? r.when.slice(0, 10) : "";
      if (d !== grid.lastDay) {
        grid.lastDay = d;
        frag.append(el("tr", { class: "day" }, el("td", { colspan: cols.length + 1 },
          d ? SS.fmtDay(r.when) : "No timestamp", el("span", { class: "muted", style: { fontWeight: 400, marginLeft: "8px" } }, plural(P.dayCounts.get(d) || 0, "turn")))));
      }
      frag.append(el("tr", {
        "data-key": r.key,
        class: `${P.checked.has(r.key) ? "checked" : ""}${P.detail === r.key ? " selected" : ""}${r.severity ? ` f-row sev-${r.severity}` : ""}`,
        title: "Click for details · double-click to show in the graph · right-click to tag or filter",
      }, WS.rowCheckbox(r.key, P.checked.has(r.key)), ...cols.map((c) => c.cell(r))));
    }
    frag.append(el("tr", { class: "more" }, el("td", { colspan: cols.length + 1 },
      to < P.rows.length ? `${fmt(to)} of ${fmt(P.rows.length)} – scroll for more` : `All ${plural(P.rows.length, "turn")}`)));
    grid.tbody.append(frag);
  }
  // make sure row `i` is in the table (keyboard moves past what has been scrolled to)
  function ensureShown(i) {
    if (i < P.shown || !grid) return;
    const from = P.shown;
    P.shown = Math.min(P.rows.length, Math.ceil((i + 1) / CHUNK) * CHUNK);
    appendRows(from, P.shown);
  }
  function onScroll() {
    const wrap = $("grid-wrap");
    if (!grid || P.shown >= P.rows.length || wrap.scrollTop + wrap.clientHeight < wrap.scrollHeight - 800) return;
    const from = P.shown;
    P.shown = Math.min(P.rows.length, P.shown + CHUNK);
    appendRows(from, P.shown);
  }

  function onGridClick(e) {
    const tr = e.target.closest("tr[data-key]");
    if (!tr || e.target.closest(".tag-menu")) return;
    const key = tr.dataset.key;
    if (e.target.closest("[data-check]")) {
      WS.toggleCheck(P.checked, P.rows.map((r) => r.key), P.anchor, key, e.shiftKey);
      P.anchor = key;
      render({ keepRail: true });
      return;
    }
    P.anchor = key;
    openDetail(key);
  }

  /* ---------------------------------------------------------------- detail */
  // row key, or a target handed over by a detail view's links
  function openDetail(key) {
    if (!key) return;
    if (key.startsWith("ent:")) key = "term:" + key;
    P.detail = key;
    renderDetail(true);
    for (const tr of document.querySelectorAll(".data-grid tbody tr[data-key]")) tr.classList.toggle("selected", tr.dataset.key === key);
  }
  function closeDetail() {
    P.detail = null;
    renderDetail();
    document.querySelectorAll(".data-grid tbody tr.selected").forEach((tr) => tr.classList.remove("selected"));
  }
  function detailView(key) {
    const opts = { onClose: closeDetail, onSelect: openDetail };
    if (key.startsWith("conv:")) return WS.convDetail(key.slice(5), opts);
    if (key.startsWith("event:")) return WS.turnDetail(key.slice(6), opts);
    const id = key.startsWith("term:") ? key.slice(5) : key;
    if (!W.nodes.has(id)) return null;
    const n = W.nodes.get(id);
    if (n.type === "conversation") return WS.convDetail(n.conv[0], opts);
    if (W.events.has(id)) return WS.turnDetail(id, opts);
    return WS.nodeDetail(id, opts);
  }
  function renderDetail(fresh = false) {
    const pane = $("detail");
    const view = P.detail && W.data ? detailView(P.detail) : null;
    if (!view) { pane.classList.add("hidden"); pane.replaceChildren(); return; }
    const keep = fresh ? 0 : pane.scrollTop;
    pane.replaceChildren(view);
    pane.classList.remove("hidden");
    pane.scrollTop = keep;
  }

  /* ---------------------------------------------------------------- render */
  function render({ keepRail = false } = {}) {
    const res = computeRows();
    P.rows = res.rows;
    P.dayCounts = new Map();
    for (const r of P.rows) { const d = r.when ? r.when.slice(0, 10) : ""; P.dayCounts.set(d, (P.dayCounts.get(d) || 0) + 1); }
    P.shown = Math.max(CHUNK, Math.min(P.shown, P.rows.length));
    if (!keepRail) renderRail();
    renderToolbar(res.inScope);
    const wrap = $("grid-wrap"), keep = wrap.scrollTop;
    renderGrid(res.counts);
    wrap.scrollTop = keep;
  }
  // a filter or the order changed: back to the top
  function changed() {
    P.shown = CHUNK;
    save();
    render();
    $("grid-wrap").scrollTop = 0;
  }

  function exportCSV() {
    const header = ["when", "kind", "label", "text", "severity", "findings", "categories", "tags", "comment", "host", "user", "agent", "conversation", "source", "target"];
    const rows = P.rows.map((r) => {
      const c = convOf(r);
      return [r.when, typeInfo(r.ev.type).label, r.ev.label, textOf(r), r.severity, r.findings.map((f) => f.label).join("; "),
        [...new Set(r.findings.map((f) => f.category))].join(" "), r.tags.join(" "), r.comment, c.host, c.user, c.harness, c.title, c.source, r.target];
    });
    SS.downloadCSV("synthsift-timeline.csv", header, rows);
    snack(`Exported ${plural(rows.length, "turn")}`);
  }

  function moveSelection(delta) {
    if (!P.rows.length) return;
    let i = rowIndex(P.detail);
    i = i < 0 ? (delta > 0 ? 0 : P.rows.length - 1) : Math.max(0, Math.min(P.rows.length - 1, i + delta));
    ensureShown(i);
    openDetail(P.rows[i].key);
    P.anchor = P.rows[i].key;
    const tr = document.querySelector(`.data-grid tr[data-key="${CSS.escape(P.rows[i].key)}"]`);
    tr && tr.scrollIntoView({ block: "nearest" });
  }
  function reveal(key) {
    const ev = key.startsWith("event:") && W.events.get(key.slice(6));
    if (ev) WS.showInGraph({ id: ev.id, para: ev.p[0] });
  }

  function wire() {
    const q = $("q");
    q.value = P.q;
    q.addEventListener("input", debounce(() => { P.q = q.value; changed(); }, 180));
    const wrap = $("grid-wrap");
    wrap.addEventListener("scroll", onScroll, { passive: true });
    wrap.addEventListener("click", onGridClick);
    wrap.addEventListener("dblclick", (e) => {
      const tr = e.target.closest("tr[data-key]");
      if (tr && !e.target.closest("[data-check]")) reveal(tr.dataset.key);
    });
    wrap.addEventListener("contextmenu", (e) => {
      const tr = e.target.closest("tr[data-key]");
      if (!tr) return;
      e.preventDefault();
      const key = tr.dataset.key;
      if (P.checked.size > 1 && P.checked.has(key)) WS.bulkMenu(e.clientX, e.clientY, checkedTargets, { withNewTag: false });
      else WS.itemMenu(key, e.clientX, e.clientY, key);
    });
    $("btn-csv").addEventListener("click", exportCSV);
    $("btn-cols").addEventListener("click", (e) => { e.stopPropagation(); WS.columnsMenu(e.currentTarget, COLS, P.hiddenCols, () => { save(); render({ keepRail: true }); }); });
    WS.wireTopbar("timeline.railClosed");
    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
      if (e.key === "Escape") { if (document.querySelector(".menu")) SS.closeMenus(); else if (!typing) closeDetail(); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); q.focus(); q.select(); }
      else if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); moveSelection(1); }
      else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); moveSelection(-1); }
      else if ((e.key === " " || e.key === "x") && P.detail && rowIndex(P.detail) >= 0) {
        e.preventDefault();
        P.checked.has(P.detail) ? P.checked.delete(P.detail) : P.checked.add(P.detail);
        render({ keepRail: true });
      } else if (e.key === "Enter" && P.detail) reveal(P.detail);
    });
    WS.on((what) => {
      if (what === "reload") P.checked.clear();
      render();
      renderDetail();
    });
  }

  async function boot() {
    wire();
    buildRail();
    try {
      await WS.load();
    } catch (e) {
      snack("Could not load the workspace: " + e.message);
    }
    filtersFromURL(WS.scopeFromURL());
    history.replaceState(null, "", location.pathname);
    render();
    WS.watchVersion();
  }
  boot();
  window.SynthSiftTimeline = { P, render };
})();
