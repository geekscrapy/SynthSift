/* Timeline page: tagged sessions, turns and terms plus security findings,
 * row by row in time order and grouped by day. The conversation scope, tag
 * filter and "hide ignored" are shared with the graph page.
 */
"use strict";

(() => {
  const { store, el, icon, snack, debounce, fmt, plural } = SS;
  const W = WS.W;
  const $ = (id) => document.getElementById(id);

  const KINDS = [
    { key: "conv", label: "Sessions", icon: "forum" },
    { key: "event", label: "Turns", icon: "chat" },
    { key: "term", label: "Terms", icon: "label" },
    { key: "finding", label: "Findings", icon: "shield" },
  ];
  const KIND_ICON = Object.fromEntries(KINDS.map((k) => [k.key, k.icon]));
  const KIND_LABEL = { conv: "Session", event: "Turn", term: "Term", finding: "Finding" };

  const P = {
    q: store.get("timeline.q", ""),
    kinds: new Set(store.get("timeline.kinds", ["conv", "event", "term", "finding"])),
    order: store.get("timeline.order", 1), // 1 = oldest first
    minSev: store.get("timeline.minSev", ""),
    secCats: new Set(store.get("timeline.secCats", [])),
    commentedOnly: store.get("timeline.commentedOnly", false),
    from: store.get("timeline.from", ""),
    to: store.get("timeline.to", ""),
    page: 0,
    pageSize: store.get("timeline.pageSize", 250),
    hiddenCols: new Set(store.get("timeline.hiddenCols", ["agent"])),
    checked: new Set(),
    anchor: null,
    detail: null, // row key
    rows: [],
  };
  const save = () => {
    for (const k of ["q", "order", "minSev", "commentedOnly", "from", "to", "pageSize"]) store.set("timeline." + k, P[k]);
    for (const k of ["kinds", "secCats", "hiddenCols"]) store.set("timeline." + k, [...P[k]]);
  };

  /* ---------------------------------------------------------------- rows */
  const ignored = (tags, conv) => tags.includes("ignore") || (conv && WS.tagsFor("conv:" + conv).includes("ignore"));

  function allRows() {
    const rows = [];
    for (const [target, a] of Object.entries(W.annotations)) {
      const kind = target.split(":", 1)[0];
      if (!KIND_LABEL[kind]) continue;
      const conv = a.conv || WS.convFor(target);
      const nodeId = kind === "term" ? target.slice(5) : kind === "event" ? target.slice(6) : target;
      const fs = kind === "event" ? W.findingsByEvent.get(target.slice(6)) || [] : [];
      rows.push({
        key: target, kind, target, nodeId, conv, when: a.ts || WS.tsFor(target) || "",
        label: a.label || WS.labelFor(target), tags: a.tags, comment: a.comment || "",
        severity: SS.worstSeverity(fs), cats: [...new Set(fs.map((f) => f.category))], finding: null,
      });
    }
    for (const f of W.findings) {
      const ev = W.events.get(f.event);
      rows.push({
        key: "f:" + f.i, kind: "finding", target: "event:" + f.event, nodeId: f.event, conv: f.conv, when: (ev && ev.ts) || "",
        label: f.label, detail: f.detail, tags: WS.tagsFor("event:" + f.event), comment: "", severity: f.severity, cats: [f.category], finding: f,
      });
    }
    return rows;
  }

  function scopeOk(r) {
    if (r.kind === "term") {
      const n = W.nodes.get(r.nodeId);
      return n ? WS.nodeVisible(n) : r.conv ? WS.convVisible(r.conv) : true;
    }
    return r.conv ? WS.convVisible(r.conv) : true;
  }

  function searchText(r) {
    const c = W.convs.get(r.conv);
    return [r.label, r.detail || "", r.comment, r.tags.join(" "), r.cats.join(" "), c ? `${c.title} ${c.host} ${c.user} ${c.harness}` : "",
      r.finding ? r.finding.chain.map((x) => `${WS.endpointLabel(x[0])} ${x[1]} ${WS.endpointLabel(x[2])}`).join(" ") : ""].join("\n");
  }

  function computeRows() {
    const rx = SS.makeRegex(P.q.trim());
    const minRank = P.minSev ? SS.sevRank(P.minSev) : -1;
    const kindCounts = new Map(), catCounts = new Map();
    const out = [];
    let total = 0;
    for (const r of allRows()) {
      if (!scopeOk(r)) continue;
      if (r.kind !== "finding") total++;
      if (W.hideIgnored && !W.tagFilter.has("ignore") && ignored(r.tags, r.conv)) continue;
      if (W.tagFilter.size && !r.tags.some((t) => W.tagFilter.has(t))) continue;
      if (P.commentedOnly && !r.comment) continue;
      const day = r.when ? r.when.slice(0, 10) : "";
      if (P.from && (!day || day < P.from)) continue;
      if (P.to && (!day || day > P.to)) continue;
      if (rx && !rx.test(searchText(r))) continue;
      if (r.kind === "finding") {
        for (const c of r.cats) catCounts.set(c, (catCounts.get(c) || 0) + 1);
        if (minRank >= 0 && SS.sevRank(r.severity) < minRank) continue;
        if (P.secCats.size && !r.cats.some((c) => P.secCats.has(c))) continue;
      }
      kindCounts.set(r.kind, (kindCounts.get(r.kind) || 0) + 1);
      if (!P.kinds.has(r.kind)) continue;
      out.push(r);
    }
    const dir = P.order;
    const kindRank = { conv: 0, event: 1, finding: 2, term: 3 };
    out.sort((a, b) => {
      if (!a.when !== !b.when) return a.when ? -1 : 1; // undated rows last
      return (a.when.localeCompare(b.when) * dir) || (kindRank[a.kind] - kindRank[b.kind]) || a.label.localeCompare(b.label);
    });
    return { rows: out, total, kindCounts, catCounts };
  }

  function filtersActive() {
    return !!(P.q || P.kinds.size < KINDS.length || P.minSev || P.secCats.size || P.commentedOnly || P.from || P.to || WS.scopeActive());
  }
  function resetFilters() {
    P.q = ""; $("q").value = "";
    P.kinds = new Set(KINDS.map((k) => k.key)); P.minSev = ""; P.secCats.clear(); P.commentedOnly = false; P.from = ""; P.to = "";
    $("min-sev").value = ""; $("d-from").value = ""; $("d-to").value = "";
    save();
    WS.resetScope();
  }

  /* ---------------------------------------------------------------- rail */
  function buildRail() {
    const sev = el("select", { class: "select", id: "min-sev", onchange: (e) => { P.minSev = e.target.value; changed(); } },
      el("option", { value: "" }, "All severities"), ...SS.SEV_ORDER.slice(1).map((s) => el("option", { value: s }, `${s[0].toUpperCase() + s.slice(1)} or worse`)));
    sev.value = P.minSev;
    const date = (id, key, label) => {
      const inp = el("input", { class: "text-input", type: "date", id, "aria-label": label, value: P[key] || null });
      inp.addEventListener("change", () => { P[key] = inp.value; changed(); });
      return inp;
    };
    $("rail").replaceChildren(
      WS.scopeSection(resetFilters),
      el("div", { class: "rail-section" },
        el("h3", {}, icon("view_list", "xs"), "Show"),
        el("div", { class: "chip-row", id: "kinds" })),
      el("div", { class: "rail-section" },
        WS.tagsHeading(),
        el("div", { class: "chip-row", id: "tagf" }),
        el("div", { class: "chip-row", style: { marginTop: "6px" } }, el("button", { id: "commented", onclick: () => { P.commentedOnly = !P.commentedOnly; changed(); } }))),
      el("div", { class: "rail-section" },
        el("h3", {}, icon("shield", "xs"), "Findings"),
        el("div", { class: "field-row" }, sev),
        el("div", { class: "chip-row", id: "cats" })),
      el("div", { class: "rail-section" },
        el("h3", {}, icon("date_range", "xs"), "Dates", el("button", { onclick: () => { P.from = P.to = ""; $("d-from").value = $("d-to").value = ""; changed(); } }, "Clear")),
        el("label", { class: "field-row" }, el("span", { style: { width: "40px" } }, "From"), date("d-from", "from", "From date")),
        el("label", { class: "field-row" }, el("span", { style: { width: "40px" } }, "To"), date("d-to", "to", "To date")),
        el("div", { class: "segmented sm", id: "order", role: "group", "aria-label": "Order", style: { marginTop: "6px" } })));
  }

  function renderRail({ kindCounts, catCounts }) {
    WS.renderScope();
    $("kinds").replaceChildren(...KINDS.map((k) => el("button", {
      class: `chip sm${P.kinds.has(k.key) ? " selected" : " off"}`, title: `Show / hide ${k.label.toLowerCase()} – shift-click to show only these`,
      onclick: (e) => {
        if (e.shiftKey) P.kinds = new Set([k.key]);
        else P.kinds.has(k.key) ? P.kinds.delete(k.key) : P.kinds.add(k.key);
        changed();
      },
    }, icon(k.icon, "xs"), k.label, el("span", { class: "count" }, fmt(kindCounts.get(k.key) || 0)))));
    WS.renderTagFilter($("tagf"));
    const cm = $("commented");
    cm.className = `chip sm${P.commentedOnly ? " selected" : ""}`;
    cm.replaceChildren(icon("comment", "xs"), "With comments only");
    WS.renderCatChips($("cats"), catCounts, P.secCats, changed);
    $("order").replaceChildren(...[[1, "Oldest first", "arrow_downward"], [-1, "Newest first", "arrow_upward"]].map(([v, label, ic]) =>
      el("button", { class: P.order === v ? "on" : "", onclick: () => { P.order = v; changed(); } }, icon(ic, "xs"), label)));
    $("reset").classList.toggle("hidden", !filtersActive());
  }

  /* --------------------------------------------------------------- columns */
  function whereEl(r) {
    const c = W.convs.get(r.conv);
    return c ? `${c.host} / ${c.user}` : "–";
  }
  const COLS = [
    {
      key: "when", label: "Time", fixed: true,
      cell: (r) => el("td", { class: "cell-muted", title: r.when || "No timestamp" }, r.when ? timeOnly(r.when) : "–"),
    },
    {
      key: "kind", label: "Kind", fixed: true,
      cell: (r) => el("td", { title: KIND_LABEL[r.kind] }, r.kind === "finding"
        ? el("span", { class: `sev-chip sev-${r.severity}` }, r.severity)
        : el("span", { class: "kind-cell" }, icon(KIND_ICON[r.kind], "xs"), KIND_LABEL[r.kind],
          r.severity ? el("span", { class: `sev-dot sev-${r.severity}`, title: `Turn has a ${r.severity} finding` }) : null)),
    },
    {
      key: "what", label: "What", fixed: true,
      cell: (r) => el("td", { class: "cell-what" },
        el("div", { class: "what" }, r.label),
        r.detail ? el("div", { class: "cell-muted wrap" }, r.detail) : null,
        r.finding && r.finding.chain.length ? WS.chainEl(r.finding.chain) : null,
        r.comment ? el("div", { class: "cmt" }, icon("comment", "xs"), r.comment) : null),
    },
    { key: "tags", label: "Tags", cell: (r) => el("td", {}, el("div", { class: "cell-tags", html: WS.tagChipsHTML(r.tags) })) },
    { key: "where", label: "Host / user", cell: (r) => el("td", { class: "cell-muted" }, whereEl(r)) },
    { key: "agent", label: "Agent", cell: (r) => el("td", { class: "cell-muted" }, (W.convs.get(r.conv) || {}).harness || "–") },
    {
      key: "conv", label: "Conversation",
      cell: (r) => {
        const c = W.convs.get(r.conv);
        return el("td", {}, c ? el("span", { class: "conv-cell", title: c.source }, el("span", { class: "dot", style: { background: c.color } }), c.title) : "–");
      },
    },
  ];
  const visibleCols = () => COLS.filter((c) => c.fixed || !P.hiddenCols.has(c.key));
  function timeOnly(ts) {
    const d = new Date(ts);
    return isNaN(d) ? ts : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  /* --------------------------------------------------------------- toolbar */
  const pageRows = () => P.rows.slice(P.page * P.pageSize, (P.page + 1) * P.pageSize);
  const rowByKey = (k) => P.rows.find((r) => r.key === k) || allRows().find((r) => r.key === k);
  const checkedTargets = () => [...new Set([...P.checked].map((k) => (k.startsWith("f:") ? "event:" + W.findings[Number(k.slice(2))].event : k)))];

  function renderToolbar(total) {
    const n = P.rows.length;
    const pager = WS.pager({ n, page: P.page, pageSize: P.pageSize, sizes: [100, 250, 500, 1000], goPage,
      onSize: (size) => { P.pageSize = size; P.page = 0; save(); render(); } });
    const days = P.dayCounts.size;
    const nf = P.rows.filter((r) => r.kind === "finding").length;
    const left = P.checked.size ? WS.bulkBar({ checked: P.checked, keys: P.rows.map((r) => r.key), canSelectAll: P.checked.size < P.rows.length,
      targets: checkedTargets, redraw: () => render({ keepRail: true }) }) : el("span", { class: "count" },
      `${plural(n - nf, "tagged row")}${nf ? ` · ${plural(nf, "finding")}` : ""} · ${plural(days, "day")}`,
      total && n - nf < total ? el("span", { class: "muted" }, ` (of ${fmt(total)} tagged)`) : null);
    $("toolbar").replaceChildren(left, el("span", { class: "grow" }), pager);
    $("q-result").textContent = P.q ? `${fmt(n)} ${n === 1 ? "row" : "rows"}` : "";
  }
  function goPage(p) {
    P.page = p;
    render({ keepRail: true });
    $("grid-wrap").scrollTop = 0;
  }

  /* ------------------------------------------------------------------ grid */
  function renderGrid() {
    const wrap = $("grid-wrap");
    if (!W.data) {
      wrap.replaceChildren(el("div", { class: "grid-empty" }, icon("timeline"), "No transcripts analysed yet. ",
        el("a", { href: "/" }, "Load or upload transcripts on the graph page"), "."));
      return;
    }
    if (!P.rows.length) {
      wrap.replaceChildren(el("div", { class: "grid-empty" }, icon("timeline"),
        filtersActive() ? "Nothing matches these filters. " : "Nothing tagged yet. Right-click a node, a turn or a term – here, on the Nodes page or in the graph – to build the timeline.",
        filtersActive() ? el("button", { class: "btn text sm", onclick: resetFilters }, "Reset filters") : null));
      return;
    }
    const cols = visibleCols();
    const rows = pageRows();
    const head = el("tr", {}, WS.pageCheckbox(rows.map((r) => r.key), P.checked, () => render({ keepRail: true })),
      ...cols.map((c) => el("th", {}, c.key === "when"
        ? el("button", { title: "Reverse the order", onclick: () => { P.order = -P.order; changed(); } }, c.label, icon(P.order > 0 ? "arrow_downward" : "arrow_upward"))
        : el("button", { style: { cursor: "default" } }, c.label))));
    const tbody = el("tbody");
    let day = null;
    for (const r of rows) {
      const d = r.when ? r.when.slice(0, 10) : "";
      if (d !== day) {
        day = d;
        const n = P.dayCounts.get(d) || 0;
        tbody.append(el("tr", { class: "day" }, el("td", { colspan: cols.length + 1 },
          d ? SS.fmtDay(r.when) : "No timestamp", el("span", { class: "muted", style: { fontWeight: 400, marginLeft: "8px" } }, plural(n, "row")))));
      }
      tbody.append(el("tr", {
        "data-key": r.key,
        class: `${P.checked.has(r.key) ? "checked" : ""}${P.detail === r.key ? " selected" : ""}${r.kind === "finding" ? ` f-row sev-${r.severity}` : ""}`,
        title: "Click for details · double-click to show in the graph · right-click to tag",
      }, WS.rowCheckbox(r.key, P.checked.has(r.key)), ...cols.map((c) => c.cell(r))));
    }
    const keep = wrap.scrollTop;
    wrap.replaceChildren(el("table", { class: "data-grid timeline-grid" }, el("thead", {}, head), tbody));
    wrap.scrollTop = keep;
  }

  function onGridClick(e) {
    const tr = e.target.closest("tr[data-key]");
    if (!tr || e.target.closest(".tag-menu")) return;
    const key = tr.dataset.key;
    if (e.target.closest("[data-check]")) {
      WS.toggleCheck(P.checked, pageRows().map((r) => r.key), P.anchor, key, e.shiftKey);
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
    if (key.startsWith("f:")) {
      const f = W.findings[Number(key.slice(2))];
      return f ? WS.turnDetail(f.event, { ...opts, finding: f }) : null;
    }
    if (key.startsWith("conv:")) return WS.convDetail(key.slice(5), opts);
    if (key.startsWith("event:")) return WS.turnDetail(key.slice(6), opts);
    const id = key.startsWith("term:") ? key.slice(5) : key;
    if (W.nodes.has(id)) {
      const n = W.nodes.get(id);
      if (n.type === "conversation") return WS.convDetail(n.conv[0], opts);
      if (W.events.has(id)) return WS.turnDetail(id, opts);
      return WS.nodeDetail(id, opts);
    }
    // a tagged term that is no longer in the graph (filtered out or below the minimum mentions)
    const a = WS.annOf(key);
    return el("div", {},
      el("div", { class: "detail-head" }, el("span", { class: "ico", style: { background: "#80868b" } }, icon("label")),
        el("div", { class: "grow" }, el("div", { class: "title" }, a ? a.label : key), el("div", { class: "sub" }, el("span", { class: "tag" }, "not in the current graph"))),
        el("button", { class: "icon-btn sm", title: "Close", onclick: closeDetail }, icon("close", "sm"))),
      el("div", { class: "detail-body" }, el("h4", {}, icon("sell", "xs"), "Tags & comment"), WS.tagEditor(key)));
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
    P.page = Math.min(P.page, Math.max(0, Math.ceil(P.rows.length / P.pageSize) - 1));
    if (!keepRail) renderRail(res);
    renderToolbar(res.total);
    renderGrid();
  }
  function changed() {
    P.page = 0;
    save();
    render();
  }

  function exportCSV() {
    const header = ["when", "kind", "severity", "label", "detail", "chain", "tags", "comment", "host", "user", "agent", "conversation", "source", "target"];
    const rows = P.rows.map((r) => {
      const c = W.convs.get(r.conv) || {};
      const chain = r.finding ? r.finding.chain.map((x) => `${WS.endpointLabel(x[0])} -${x[1]}-> ${WS.endpointLabel(x[2])}`).join("; ") : "";
      return [r.when, KIND_LABEL[r.kind], r.severity, r.label, r.detail || "", chain, r.tags.join(" "), r.comment, c.host, c.user, c.harness, c.title, c.source, r.target];
    });
    SS.downloadCSV("synthsift-timeline.csv", header, rows);
    snack(`Exported ${plural(rows.length, "row")}`);
  }

  function moveSelection(delta) {
    const rows = pageRows();
    if (!rows.length) return;
    let i = rows.findIndex((r) => r.key === P.detail);
    i = i < 0 ? (delta > 0 ? 0 : rows.length - 1) : Math.max(0, Math.min(rows.length - 1, i + delta));
    openDetail(rows[i].key);
    P.anchor = rows[i].key;
    const tr = document.querySelector(`.data-grid tr[data-key="${CSS.escape(rows[i].key)}"]`);
    tr && tr.scrollIntoView({ block: "nearest" });
  }
  function reveal(key) {
    const r = rowByKey(key);
    if (!r) return;
    const ev = W.events.get(r.nodeId);
    WS.showInGraph({ id: r.kind === "term" ? r.nodeId : r.kind === "conv" ? r.target : r.nodeId, para: ev ? ev.p[0] : null });
  }

  function wire() {
    const q = $("q");
    q.value = P.q;
    q.addEventListener("input", debounce(() => { P.q = q.value; changed(); }, 180));
    const grid = $("grid-wrap");
    grid.addEventListener("click", onGridClick);
    grid.addEventListener("dblclick", (e) => {
      const tr = e.target.closest("tr[data-key]");
      if (tr && !e.target.closest("[data-check]")) reveal(tr.dataset.key);
    });
    grid.addEventListener("contextmenu", (e) => {
      const tr = e.target.closest("tr[data-key]");
      if (!tr) return;
      e.preventDefault();
      if (P.checked.size > 1 && P.checked.has(tr.dataset.key)) WS.bulkMenu(e.clientX, e.clientY, checkedTargets, { withNewTag: false });
      else { const r = rowByKey(tr.dataset.key); if (r) WS.openTagMenu(r.target, e.clientX, e.clientY); }
    });
    $("btn-csv").addEventListener("click", exportCSV);
    $("btn-cols").addEventListener("click", (e) => { e.stopPropagation(); WS.columnsMenu(e.currentTarget, COLS, P.hiddenCols, () => { save(); renderGrid(); }); });
    WS.wireTopbar("timeline.railClosed");
    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
      if (e.key === "Escape") { if (document.querySelector(".menu")) SS.closeMenus(); else if (!typing) closeDetail(); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); q.focus(); q.select(); }
      else if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); moveSelection(1); }
      else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); moveSelection(-1); }
      else if ((e.key === " " || e.key === "x") && P.detail && P.rows.some((r) => r.key === P.detail)) {
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
    render();
    WS.watchVersion();
  }
  boot();
  window.SynthSiftTimeline = { P, render };
})();
