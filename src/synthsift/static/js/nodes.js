/* Nodes page: every node of the analysed graph in one sortable, filterable,
 * taggable table with a detail pane. The conversation scope, time window, tag
 * filter and "hide ignored" are shared with the other pages; the rest is
 * remembered per page.
 *
 * URL parameters (the dashboard's links): the shared scope (from, to, host, …) and
 * kinds=<node types / categories to show>, lists=<list labels or *>, cats=<finding categories>,
 * sev=<minimum severity>, q=<search>, select=<node id>.
 */
"use strict";

(() => {
  const { store, el, icon, snack, debounce, fmt, plural } = SS;
  const W = WS.W;
  const $ = (id) => document.getElementById(id);

  const P = {
    q: store.get("nodes.q", ""),
    sort: store.get("nodes.sort", { key: "mentions", dir: -1 }),
    page: 0,
    pageSize: store.get("nodes.pageSize", 100),
    hiddenKinds: new Set(store.get("nodes.hiddenKinds", [])),
    hiddenLayers: new Set(store.get("nodes.hiddenLayers", [])),
    tagMode: store.get("nodes.tagMode", "all"),
    minSev: store.get("nodes.minSev", ""),
    secCats: new Set(store.get("nodes.secCats", [])),
    lists: new Set(store.get("nodes.lists", [])), // IOC / keyword list labels
    minMentions: store.get("nodes.minMentions", 0),
    hiddenCols: new Set(store.get("nodes.hiddenCols", ["layer"])),
    checked: new Set(),
    anchor: null, // last row clicked, for shift-click ranges
    detail: null, // { kind: "node" | "conv" | "turn", id }
    rows: [],
  };
  const save = () => {
    for (const k of ["q", "pageSize", "tagMode", "minSev", "minMentions"]) store.set("nodes." + k, P[k]);
    for (const k of ["hiddenKinds", "hiddenLayers", "secCats", "lists", "hiddenCols"]) store.set("nodes." + k, [...P[k]]);
    store.set("nodes.sort", P.sort);
  };

  const LAYER_LABEL = Object.fromEntries(SS.LAYERS.map((l) => [l.key, l.label]));
  const TAG_MODES = [["all", "All"], ["tagged", "Tagged"], ["untagged", "Untagged"], ["commented", "Commented"]];

  /* ------------------------------------------------ per-node derived data */
  let meta = new Map();
  function buildMeta() {
    meta = new Map();
    for (const n of W.nodes.values()) {
      const kind = WS.kindOf(n);
      const [first, last] = WS.seenRange(n.id);
      const fs = WS.findingsForNode(n.id);
      meta.set(n.id, {
        kind, first, last,
        group: WS.kindGroup(kind.key),
        mentions: n.type === "entity" ? n.count || 0 : (n.occ || []).length,
        fcount: fs.length,
        sev: n.sec || SS.worstSeverity(fs) || "",
        text: null,
      });
    }
  }
  // searchable text of a structural node: its paragraphs (entities match on the name)
  function textOf(n) {
    const m = meta.get(n.id);
    if (m.text === null) {
      if (n.type === "conversation") { const c = W.convs.get(n.conv[0]); m.text = c ? `${c.title}\n${c.host}\n${c.user}\n${c.harness}\n${c.source}` : ""; }
      else m.text = (n.occ || []).map(([pid]) => (W.paras.get(pid) || {}).t || "").join("\n").slice(0, 20000);
    }
    return m.text;
  }

  /* -------------------------------------------------------------- columns */
  function whereOf(r) {
    if (!r.convs.length) return "";
    const cs = r.convs.map((c) => W.convs.get(c));
    const hosts = new Set(cs.map((c) => c.host)), users = new Set(cs.map((c) => c.user));
    return hosts.size === 1 && users.size === 1 ? `${cs[0].host} / ${cs[0].user}` : `${plural(hosts.size, "host")} · ${plural(users.size, "user")}`;
  }
  const COLS = [
    { key: "label", label: "Node", fixed: true, get: (r) => r.n.label.toLowerCase(), cell: nodeCell },
    { key: "type", label: "Type", get: (r) => r.m.kind.label, cell: (r) => el("td", { class: "cell-muted" }, r.m.kind.label) },
    {
      key: "findings", label: "Findings", get: (r) => (r.m.sev ? (SS.sevRank(r.m.sev) + 1) * 1e6 : 0) + r.m.fcount, desc: true,
      cell: (r) => el("td", { class: "nowrap", title: (r.n.secc || []).join(", ") }, r.m.sev ? el("span", { class: `sev-chip sev-${r.m.sev}` }, r.m.sev) : "",
        r.m.fcount > 1 ? el("span", { class: "cell-muted" }, ` ×${fmt(r.m.fcount)}`) : ""),
    },
    {
      key: "tags", label: "Tags", get: (r) => (r.tags.length ? r.tags.slice().sort().join(" ") : ""),
      cell: (r) => el("td", {}, el("div", { class: "cell-tags", html: WS.tagChipsHTML(r.tags, inheritedTags(r)) },
        r.ann && r.ann.comment ? el("span", { class: "msi", title: r.ann.comment }, "comment") : null)),
    },
    {
      key: "lists", label: "Lists", get: (r) => (r.n.labels || []).join(" "),
      cell: (r) => el("td", {}, el("div", { class: "cell-tags" }, (r.n.labels || []).map((l) => el("span", { class: "tag warn", title: "On an IOC / keyword list" }, icon("playlist_add_check", "xs"), l)))),
    },
    { key: "layer", label: "Layer", get: (r) => LAYER_LABEL[r.n.layer] || "", cell: (r) => el("td", { class: "cell-muted" }, LAYER_LABEL[r.n.layer] || "–") },
    { key: "mentions", label: "Mentions", num: true, get: (r) => r.m.mentions, cell: (r) => el("td", { class: "num" }, fmt(r.m.mentions)) },
    { key: "links", label: "Links", num: true, get: (r) => r.n.deg || 0, cell: (r) => el("td", { class: "num" }, fmt(r.n.deg || 0)) },
    {
      key: "convs", label: "Conversations", num: true, get: (r) => r.convs.length,
      cell: (r) => el("td", { class: "num", title: r.convs.slice(0, 12).map((c) => W.convs.get(c).title).join("\n") }, fmt(r.convs.length)),
    },
    { key: "where", label: "Host / user", get: whereOf, cell: (r) => el("td", { class: "cell-muted" }, whereOf(r) || "–") },
    { key: "first", label: "First seen", get: (r) => r.m.first || "", cell: (r) => el("td", { class: "cell-muted" }, SS.fmtTime(r.m.first) || "–") },
    { key: "last", label: "Last seen", get: (r) => r.m.last || "", desc: true, cell: (r) => el("td", { class: "cell-muted" }, SS.fmtTime(r.m.last) || "–") },
  ];
  // session tags shown on a turn that doesn't carry them itself
  function inheritedTags(r) {
    const own = new Set(WS.tagsFor(r.target));
    return new Set(r.tags.filter((t) => !own.has(t)));
  }
  const visibleCols = () => COLS.filter((c) => c.fixed || !P.hiddenCols.has(c.key));

  function nodeCell(r) {
    const k = r.m.kind;
    const mono = r.m.group === "Technical" || r.n.type === "tool_arg" || r.n.type === "tool_call";
    const color = r.n.type === "conversation" && W.convs.get(r.n.conv[0]) ? W.convs.get(r.n.conv[0]).color : k.color;
    return el("td", {}, el("div", { class: "cell-node" },
      el("span", { class: "ico", style: { background: color } }, icon(k.icon || "label")),
      el("span", { class: `lbl${mono ? " mono" : ""}`, title: r.n.label }, r.n.label)));
  }

  /* -------------------------------------------------------------- filters */
  function computeRows() {
    const rx = SS.makeRegex(P.q.trim());
    const minRank = P.minSev ? SS.sevRank(P.minSev) : -1;
    const kindCounts = new Map(), layerCounts = new Map(), catCounts = new Map(), listCounts = new Map();
    const rows = [];
    let total = 0;
    const windowed = WS.windowOn();
    for (const n of W.nodes.values()) {
      if (!WS.nodeVisible(n)) continue;
      total++;
      let m = meta.get(n.id);
      // with a time window, entities count their mentions inside it
      if (windowed && n.type === "entity") m = { ...m, mentions: (n.occ || []).filter(([pid]) => WS.paraVisible(pid)).length };
      const tags = WS.nodeTags(n.id);
      if (W.hideIgnored && tags.includes("ignore") && !W.tagFilter.has("ignore")) continue;
      if (W.tagFilter.size && !tags.some((t) => W.tagFilter.has(t))) continue;
      const target = WS.targetOf(n.id);
      const ann = WS.annOf(target);
      if (P.tagMode === "tagged" && !tags.length) continue;
      if (P.tagMode === "untagged" && tags.length) continue;
      if (P.tagMode === "commented" && !(ann && ann.comment)) continue;
      if (n.type === "entity" && m.mentions < P.minMentions) continue;
      if (rx && !rx.test(n.label) && !(n.type !== "entity" && rx.test(textOf(n)))) continue;
      if (m.sev) for (const c of n.secc || []) catCounts.set(c, (catCounts.get(c) || 0) + 1);
      if (minRank >= 0 && !(m.sev && SS.sevRank(m.sev) >= minRank)) continue;
      if (P.secCats.size && !(n.secc || []).some((c) => P.secCats.has(c))) continue;
      for (const l of n.labels || []) listCounts.set(l, (listCounts.get(l) || 0) + 1);
      if (P.lists.size && !(n.labels || []).some((l) => P.lists.has(l))) continue;
      kindCounts.set(m.kind.key, (kindCounts.get(m.kind.key) || 0) + 1);
      layerCounts.set(n.layer, (layerCounts.get(n.layer) || 0) + 1);
      if (P.hiddenLayers.has(n.layer) || P.hiddenKinds.has(m.kind.key)) continue;
      rows.push({ id: n.id, n, m, tags, ann, target, convs: (n.conv || []).filter(WS.convVisible) });
    }
    const col = COLS.find((c) => c.key === P.sort.key) || COLS[3];
    const dir = P.sort.dir;
    rows.sort((a, b) => {
      const x = col.get(a), y = col.get(b);
      if (x === "" && y !== "") return 1; // blanks last in either direction
      if (y === "" && x !== "") return -1;
      const d = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return d * dir || a.n.label.localeCompare(b.n.label);
    });
    return { rows, total, kindCounts, layerCounts, catCounts, listCounts };
  }

  function filtersActive() {
    return !!(P.q || P.hiddenKinds.size || P.hiddenLayers.size || P.tagMode !== "all" || P.minSev || P.secCats.size || P.lists.size || P.minMentions
      || WS.scopeActive());
  }
  function resetPageFilters() {
    P.q = ""; $("q").value = "";
    P.hiddenKinds.clear(); P.hiddenLayers.clear(); P.tagMode = "all"; P.minSev = ""; P.secCats.clear(); P.lists.clear(); P.minMentions = 0;
    $("min-mentions").value = 0; $("min-sev").value = "";
    save();
  }
  function resetFilters() {
    resetPageFilters();
    WS.resetScope();
  }
  /** a dashboard link: only the page filters it names apply */
  function filtersFromURL(params) {
    if (!["kinds", "lists", "cats", "sev", "q"].some((k) => params.has(k))) return;
    resetPageFilters();
    const list = (k) => (params.get(k) || "").split(",").filter(Boolean);
    if (params.has("kinds")) {
      const show = new Set(list("kinds"));
      for (const m of meta.values()) if (!show.has(m.kind.key)) P.hiddenKinds.add(m.kind.key);
    }
    if (params.has("lists")) {
      const all = params.get("lists") === "*";
      P.lists = new Set(all ? [...W.nodes.values()].flatMap((n) => n.labels || []) : list("lists"));
    }
    if (params.has("cats")) P.secCats = new Set(list("cats"));
    if (params.has("sev")) { P.minSev = params.get("sev"); $("min-sev").value = P.minSev; }
    if (params.has("q")) { P.q = params.get("q"); $("q").value = P.q; }
    save();
  }

  /* ----------------------------------------------------------------- rail */
  function buildRail() {
    const sev = el("select", { class: "select", id: "min-sev", onchange: (e) => { P.minSev = e.target.value; changed(); } },
      el("option", { value: "" }, "Any (flagged or not)"),
      ...SS.SEV_ORDER.map((s) => el("option", { value: s }, s === "info" ? "Flagged (any severity)" : `Flagged ${s} or worse`)));
    sev.value = P.minSev;
    const mm = el("input", { class: "text-input", id: "min-mentions", type: "number", min: 0, step: 1, value: P.minMentions, style: { width: "84px" } });
    mm.addEventListener("input", debounce(() => { P.minMentions = Math.max(0, Number(mm.value) || 0); changed(); }, 200));
    $("rail").replaceChildren(
      WS.scopeSection(resetFilters),
      el("div", { class: "rail-section" },
        WS.tagsHeading(),
        el("div", { class: "chip-row", id: "tagf" }),
        el("div", { class: "segmented sm", id: "tagmode", role: "group", "aria-label": "Tag state", style: { marginTop: "8px" } })),
      el("div", { class: "rail-section" },
        el("h3", {}, icon("shield", "xs"), "Security"),
        el("div", { class: "field-row" }, sev),
        el("div", { class: "chip-row", id: "cats" })),
      el("div", { class: "rail-section hidden", id: "lists-sec" },
        el("h3", {}, icon("playlist_add_check", "xs"), "Lists", el("a", { href: "/settings#modules", title: "Manage IOC / keyword lists" }, "Manage")),
        el("div", { class: "chip-row", id: "listf" })),
      el("div", { class: "rail-section" },
        el("h3", {}, icon("category", "xs"), "Node types", el("button", { onclick: () => { P.hiddenKinds.clear(); P.hiddenLayers.clear(); changed(); } }, "Show all")),
        el("div", { class: "chip-row", id: "layers" }), el("div", { id: "kinds", style: { marginTop: "6px" } })),
      el("div", { class: "rail-section" },
        el("h3", {}, icon("tag", "xs"), "Terms"),
        el("label", { class: "field-row" }, el("span", { class: "grow" }, "Minimum mentions"), mm)));
  }

  function renderRail({ kindCounts, layerCounts, catCounts, listCounts }) {
    WS.renderScope();
    WS.renderTagFilter($("tagf"));
    $("tagmode").replaceChildren(...TAG_MODES.map(([k, label]) => el("button", {
      class: P.tagMode === k ? "on" : "", onclick: () => { P.tagMode = k; changed(); },
    }, label)));
    WS.renderCatChips($("cats"), catCounts, P.secCats, changed);
    const lists = [...new Set([...listCounts.keys(), ...P.lists])].sort();
    $("lists-sec").classList.toggle("hidden", !lists.length);
    $("listf").replaceChildren(...lists.map((l) => el("button", {
      class: `chip sm${P.lists.has(l) ? " selected" : ""}`, title: `Only terms on “${l}”`,
      onclick: () => { P.lists.has(l) ? P.lists.delete(l) : P.lists.add(l); changed(); },
    }, el("span", { class: "label" }, l), el("span", { class: "count" }, fmt(listCounts.get(l) || 0)))));
    $("layers").replaceChildren(...SS.LAYERS.map((l) => el("button", {
      class: `chip sm${P.hiddenLayers.has(l.key) ? " off" : " selected"}`, title: `Show / hide the ${l.label.toLowerCase()} layer`,
      onclick: () => { P.hiddenLayers.has(l.key) ? P.hiddenLayers.delete(l.key) : P.hiddenLayers.add(l.key); changed(); },
    }, icon(P.hiddenLayers.has(l.key) ? "visibility_off" : l.icon, "xs"), l.label, el("span", { class: "count" }, fmt(layerCounts.get(l.key) || 0)))));
    const groups = new Map();
    for (const [key, n] of kindCounts) {
      const g = WS.kindGroup(key);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push({ ...W.kinds.get(key), key, n });
    }
    for (const key of P.hiddenKinds) { // keep hidden kinds visible so they can be switched back on
      if (kindCounts.has(key) || !W.kinds.has(key)) continue;
      const g = WS.kindGroup(key);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push({ ...W.kinds.get(key), key, n: 0 });
    }
    $("kinds").replaceChildren(...[...groups.keys()].sort((a, b) => SS.KIND_GROUPS.indexOf(a) - SS.KIND_GROUPS.indexOf(b)).map((g) => {
      const items = groups.get(g).sort((a, b) => b.n - a.n);
      const allOn = items.every((i) => !P.hiddenKinds.has(i.key));
      return el("div", { class: "rail-group" },
        el("h4", {}, g, el("button", { onclick: () => { for (const i of items) allOn ? P.hiddenKinds.add(i.key) : P.hiddenKinds.delete(i.key); changed(); } }, allOn ? "hide all" : "show all")),
        el("div", { class: "chip-row" }, items.map((i) => el("button", {
          class: `chip sm${P.hiddenKinds.has(i.key) ? " off" : ""}`, title: `${i.label} – click to toggle, shift-click to show only this type`,
          onclick: (ev) => {
            if (ev.shiftKey) { for (const k of W.kinds.keys()) P.hiddenKinds.add(k); P.hiddenKinds.delete(i.key); }
            else P.hiddenKinds.has(i.key) ? P.hiddenKinds.delete(i.key) : P.hiddenKinds.add(i.key);
            changed();
          },
        }, el("span", { class: "swatch", style: { background: i.color } }), el("span", { class: "label" }, i.label), el("span", { class: "count" }, fmt(i.n))))));
    }));
    $("reset").classList.toggle("hidden", !filtersActive());
  }

  /* -------------------------------------------------------------- toolbar */
  const pageRows = () => P.rows.slice(P.page * P.pageSize, (P.page + 1) * P.pageSize);
  const checkedTargets = () => [...new Set([...P.checked].map(WS.targetOf).filter(Boolean))];

  function renderToolbar(total) {
    const n = P.rows.length;
    const pager = WS.pager({ n, page: P.page, pageSize: P.pageSize, sizes: [50, 100, 250, 500, 1000], firstLast: true, goPage,
      onSize: (size) => { P.pageSize = size; P.page = 0; save(); render(); } });
    const left = P.checked.size ? WS.bulkBar({ checked: P.checked, keys: P.rows.map((r) => r.id), canSelectAll: P.rows.some((r) => !P.checked.has(r.id)),
      targets: checkedTargets, redraw: () => render({ keepRail: true }) }) : el("span", { class: "count" },
      n === total ? plural(n, "node") : `${fmt(n)} of ${plural(total, "node")}`);
    $("toolbar").replaceChildren(left, el("span", { class: "grow" }), pager);
    $("q-result").textContent = P.q ? `${fmt(n)} ${n === 1 ? "node" : "nodes"}` : "";
  }
  function goPage(p) {
    P.page = p;
    render({ keepRail: true });
    $("grid-wrap").scrollTop = 0;
  }

  /* ----------------------------------------------------------------- grid */
  function renderGrid() {
    const wrap = $("grid-wrap");
    if (!W.data) {
      wrap.replaceChildren(el("div", { class: "grid-empty" }, icon("table_rows"), "No transcripts analysed yet. ",
        el("a", { href: "/" }, "Load or upload transcripts on the graph page"), "."));
      return;
    }
    if (!P.rows.length) {
      wrap.replaceChildren(el("div", { class: "grid-empty" }, icon("filter_list_off"), "No nodes match these filters. ",
        filtersActive() ? el("button", { class: "btn text sm", onclick: resetFilters }, "Reset filters") : null));
      return;
    }
    const cols = visibleCols();
    const rows = pageRows();
    const head = el("tr", {}, WS.pageCheckbox(rows.map((r) => r.id), P.checked, () => render({ keepRail: true })),
      ...cols.map((c) => {
        const on = P.sort.key === c.key;
        return el("th", { class: `${c.num ? "num" : ""}${on ? " sorted" : ""}`, "aria-sort": on ? (P.sort.dir > 0 ? "ascending" : "descending") : "none" },
          el("button", {
            title: `Sort by ${c.label.toLowerCase()}`,
            onclick: () => {
              P.sort = on ? { key: c.key, dir: -P.sort.dir } : { key: c.key, dir: c.num || c.desc ? -1 : 1 };
              P.page = 0; save(); render({ keepRail: true });
            },
          }, c.label, on ? icon(P.sort.dir > 0 ? "arrow_upward" : "arrow_downward") : null));
      }));
    const tbody = el("tbody");
    for (const r of rows) {
      const tr = el("tr", {
        "data-id": r.id,
        class: `${P.checked.has(r.id) ? "checked" : ""}${P.detail && P.detail.id === r.id ? " selected" : ""}`,
        title: "Click for details · double-click to show in the graph · right-click to tag",
      }, WS.rowCheckbox(r.id, P.checked.has(r.id)), ...cols.map((c) => c.cell(r)));
      tbody.append(tr);
    }
    const keep = wrap.scrollTop;
    wrap.replaceChildren(el("table", { class: "data-grid" }, el("thead", {}, head), tbody));
    wrap.scrollTop = keep;
  }

  function onGridClick(e) {
    const cb = e.target.closest("[data-check]");
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    const id = tr.dataset.id;
    if (cb) {
      WS.toggleCheck(P.checked, pageRows().map((r) => r.id), P.anchor, id, e.shiftKey);
      P.anchor = id;
      render({ keepRail: true });
      return;
    }
    P.anchor = id;
    openDetail(id);
  }

  /* --------------------------------------------------------------- detail */
  function openDetail(target) {
    if (!target) return;
    if (target.startsWith("event:")) {
      const id = target.slice(6);
      P.detail = W.nodes.has(id) ? { kind: "node", id } : { kind: "turn", id };
    } else if (target.startsWith("term:")) P.detail = { kind: "node", id: target.slice(5) };
    else if (W.nodes.has(target)) P.detail = { kind: W.nodes.get(target).type === "conversation" ? "conv" : "node", id: target };
    else if (target.startsWith("conv:")) P.detail = { kind: "conv", id: target };
    else return;
    renderDetail(true);
    for (const tr of document.querySelectorAll(".data-grid tbody tr")) tr.classList.toggle("selected", tr.dataset.id === P.detail.id);
  }
  function closeDetail() {
    P.detail = null;
    renderDetail();
    document.querySelectorAll(".data-grid tbody tr.selected").forEach((tr) => tr.classList.remove("selected"));
  }
  function renderDetail(fresh = false) {
    const pane = $("detail");
    if (!P.detail || !W.data) { pane.classList.add("hidden"); pane.replaceChildren(); return; }
    const d = P.detail;
    const opts = { onClose: closeDetail, onSelect: openDetail };
    const keep = fresh ? 0 : pane.scrollTop;
    const view = d.kind === "conv" ? WS.convDetail(d.id.slice(5), opts) : d.kind === "turn" ? WS.turnDetail(d.id, opts) : WS.nodeDetail(d.id, opts);
    pane.replaceChildren(view);
    pane.classList.remove("hidden");
    pane.scrollTop = keep;
  }

  /* --------------------------------------------------------------- render */
  function render({ keepRail = false } = {}) {
    const res = computeRows();
    P.rows = res.rows;
    const pages = Math.max(1, Math.ceil(P.rows.length / P.pageSize));
    P.page = Math.min(P.page, pages - 1);
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
    const header = ["id", "label", "type", "layer", "mentions", "links", "conversations", "hosts", "users", "severity", "findings", "categories",
      "tags", "comment", "first_seen", "last_seen"];
    const rows = P.rows.map((r) => {
      const cs = r.convs.map((c) => W.convs.get(c));
      return [r.id, r.n.label, r.m.kind.label, LAYER_LABEL[r.n.layer] || "", r.m.mentions, r.n.deg || 0, cs.map((c) => c.title).join(" | "),
        [...new Set(cs.map((c) => c.host))].join(" "), [...new Set(cs.map((c) => c.user))].join(" "), r.m.sev, r.m.fcount,
        (r.n.secc || []).join(" "), r.tags.join(" "), r.ann ? r.ann.comment : "", r.m.first || "", r.m.last || ""];
    });
    SS.downloadCSV("synthsift-nodes.csv", header, rows);
    snack(`Exported ${plural(rows.length, "node")}`);
  }

  /* ------------------------------------------------------------------ keys */
  function moveSelection(delta) {
    const rows = pageRows();
    if (!rows.length) return;
    let i = P.detail ? rows.findIndex((r) => r.id === P.detail.id) : -1;
    i = i < 0 ? (delta > 0 ? 0 : rows.length - 1) : Math.max(0, Math.min(rows.length - 1, i + delta));
    openDetail(rows[i].id);
    P.anchor = rows[i].id;
    const tr = document.querySelector(`.data-grid tr[data-id="${CSS.escape(rows[i].id)}"]`);
    tr && tr.scrollIntoView({ block: "nearest" });
  }

  function wire() {
    const q = $("q");
    q.value = P.q;
    q.addEventListener("input", debounce(() => { P.q = q.value; changed(); }, 180));
    $("grid-wrap").addEventListener("click", onGridClick);
    $("grid-wrap").addEventListener("dblclick", (e) => {
      const tr = e.target.closest("tr[data-id]");
      if (tr && !e.target.closest("[data-check]")) WS.showInGraph({ id: tr.dataset.id });
    });
    $("grid-wrap").addEventListener("contextmenu", (e) => {
      const tr = e.target.closest("tr[data-id]");
      if (!tr) return;
      e.preventDefault();
      if (P.checked.size > 1 && P.checked.has(tr.dataset.id)) WS.bulkMenu(e.clientX, e.clientY, checkedTargets);
      else WS.itemMenu(tr.dataset.id, e.clientX, e.clientY, WS.targetOf(tr.dataset.id));
    });
    $("btn-csv").addEventListener("click", exportCSV);
    $("btn-cols").addEventListener("click", (e) => { e.stopPropagation(); WS.columnsMenu(e.currentTarget, COLS, P.hiddenCols, () => { save(); renderGrid(); }); });
    WS.wireTopbar("nodes.railClosed");
    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
      if (e.key === "Escape") { if (document.querySelector(".menu")) SS.closeMenus(); else if (!typing) closeDetail(); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); q.focus(); q.select(); }
      else if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); moveSelection(1); }
      else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); moveSelection(-1); }
      else if ((e.key === " " || e.key === "x") && P.detail) {
        e.preventDefault();
        P.checked.has(P.detail.id) ? P.checked.delete(P.detail.id) : P.checked.add(P.detail.id);
        render({ keepRail: true });
      } else if (e.key === "Enter" && P.detail) WS.showInGraph({ id: P.detail.id });
      else if (e.key === "ArrowRight" && P.page < Math.ceil(P.rows.length / P.pageSize) - 1) goPage(P.page + 1);
      else if (e.key === "ArrowLeft" && P.page > 0) goPage(P.page - 1);
    });
    WS.on((what) => {
      if (what === "reload") { buildMeta(); P.checked = new Set([...P.checked].filter((id) => W.nodes.has(id))); }
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
    buildMeta();
    const params = WS.scopeFromURL();
    filtersFromURL(params);
    history.replaceState(null, "", location.pathname);
    render();
    const sel = params.get("select");
    if (sel) {
      const id = W.nodes.has(sel) ? sel : W.nodes.has(sel.replace(/^(term|event):/, "")) ? sel.replace(/^(term|event):/, "") : null;
      if (id) {
        const i = P.rows.findIndex((r) => r.id === id);
        if (i >= 0 && Math.floor(i / P.pageSize) !== P.page) goPage(Math.floor(i / P.pageSize));
        openDetail(id);
        const tr = document.querySelector(`.data-grid tr[data-id="${CSS.escape(id)}"]`);
        tr && tr.scrollIntoView({ block: "center" });
      }
    }
    WS.watchVersion();
  }
  boot();
  window.SynthSiftNodes = { P, render };
})();
