/* Shared workspace model for the full-page views (Nodes, Timeline, Dashboard).
 *
 * Loads the analysed graph, settings and analyst annotations, and exposes the
 * same filter / tag / comment semantics as the graph page (the lookups and tag
 * helpers are SS.model, shared with it). Filters and tags use the same
 * localStorage keys and BroadcastChannel messages as the graph page, so every
 * open page stays in sync.
 */
"use strict";

const WS = (() => {
  const { store, api, esc, el, icon, snack, fmt, plural, fmtTime } = SS;

  const W = {
    data: null,
    settings: {},
    kinds: new Map(),
    convs: new Map(),
    convOrder: [],
    events: new Map(),
    paras: new Map(),
    nodes: new Map(),
    findings: [],
    findingsByEvent: new Map(),
    findingsByNode: new Map(),
    annotations: {},
    tags: [],
    filter: { ...SS.emptyScope(), ...store.get("convFilter", {}) },
    hiddenConvs: new Set(store.get("hiddenConvs", [])),
    tagFilter: new Set(store.get("tagFilter", [])),
    hideIgnored: store.get("hideIgnored", true),
    listeners: new Set(),
  };
  const M = SS.model(W, { onSaved: () => { broadcast({ type: "annotations" }); emit("annotations"); }, promptTag: newTag });
  const { kindOf, convVisible, paraVisible, targetOf, annOf, tagsFor, labelFor, seenRange, loadAnnotations, tagChipsHTML, chainEl } = M;

  /* ------------------------------------------------------------- loading */
  async function load() {
    const [st, g] = await Promise.all([api("/api/settings"), fetch("/api/graph").then((r) => r.json())]);
    W.settings = st.values || {};
    SS.applyTheme(store.get("theme", W.settings.theme || "auto"));
    await loadAnnotations();
    ingest(g);
    return W;
  }

  function ingest(g) {
    W.data = g.empty ? null : g;
    W.kinds.clear(); W.convs.clear(); W.events.clear(); W.paras.clear(); W.nodes.clear();
    W.findings = []; W.findingsByEvent.clear(); W.findingsByNode.clear(); W.convOrder = [];
    if (!W.data) return;
    SS.loadKinds(W.kinds, g, W.settings);
    W.convOrder = g.conversations.map((c) => c.id);
    for (const c of g.conversations) W.convs.set(c.id, c);
    for (const e of g.events) W.events.set(e.id, e);
    for (const p of g.paragraphs) W.paras.set(p.id, p);
    for (const n of g.nodes) W.nodes.set(n.id, n);
    W.findings = (g.findings || []).map((f, i) => ({ ...f, i }));
    // findings per event and per entity node (resolved the way the graph builder does)
    const add = (m, k, f) => { if (!m.has(k)) m.set(k, []); if (!m.get(k).includes(f)) m.get(k).push(f); };
    for (const f of W.findings) {
      add(W.findingsByEvent, f.event, f);
      for (const key of new Set([...f.entities, ...f.chain.flatMap((c) => [c[0], c[2]])])) {
        const id = W.nodes.has("ent:" + key) ? "ent:" + key : "ent:" + f.conv + ":" + key;
        if (W.nodes.has(id)) add(W.findingsByNode, id, f);
      }
    }
    // neighbours for the detail views
    W.neighbours = new Map();
    for (const e of g.edges) {
      for (const [a, b] of [[e.from, e.to], [e.to, e.from]]) {
        if (!W.neighbours.has(a)) W.neighbours.set(a, []);
        W.neighbours.get(a).push({ id: b, type: e.type, label: e.label || "" });
      }
    }
  }

  /* ------------------------------------------------------------ filters */
  const nodeVisible = (n) => (!(n.conv && n.conv.length) || n.conv.some(convVisible)) && M.nodeInWindow(n);

  /** apply scope parameters from the page URL (?from=…&to=…&host=…) to the shared filter; returns the parameters */
  function scopeFromURL() {
    const params = new URLSearchParams(location.search);
    const patch = SS.scopeFromURL(params);
    if (patch) setFilter(patch);
    return params;
  }

  function setFilter(patch, { broadcastIt = true } = {}) {
    W.filter = SS.narrowScope(W.filter, patch);
    store.set("convFilter", W.filter);
    if (broadcastIt) broadcast({ type: "filters", hiddenConvs: [...W.hiddenConvs], filter: W.filter });
    emit("filters");
  }

  /** host / user / agent selects, same behaviour as the graph page */
  function renderFilterSelects(container) {
    const all = W.convOrder.map((c) => W.convs.get(c));
    const f = W.filter;
    const scopes = {
      host: all,
      user: all.filter((c) => !f.host || c.host === f.host),
      harness: all.filter((c) => (!f.host || c.host === f.host) && (!f.user || c.user === f.user)),
    };
    const rows = [];
    for (const [key, ic, label] of [["host", "computer", "All hosts"], ["user", "person", "All users"], ["harness", "terminal", "All agents"]]) {
      const vals = [...new Set(scopes[key].map((c) => c[key]))].sort();
      if (f[key] && !vals.includes(f[key])) f[key] = "";
      const sel = el("select", { "aria-label": label, onchange: (e) => setFilter({ [key]: e.target.value }) },
        el("option", { value: "" }, `${label} (${vals.length})`), ...vals.map((v) => el("option", { value: v, selected: f[key] === v }, v)));
      rows.push(el("label", { class: `mini-select${f[key] ? " active" : ""}`, title: label }, icon(ic, "xs"), sel));
    }
    const convSel = el("select", { "aria-label": "Conversation", onchange: (e) => setFilter({ conv: e.target.value }) },
      el("option", { value: "" }, "All conversations"),
      ...all.filter((c) => (!f.host || c.host === f.host) && (!f.user || c.user === f.user) && (!f.harness || c.harness === f.harness))
        .map((c) => el("option", { value: c.id, selected: f.conv === c.id }, c.title)));
    container.replaceChildren(el("div", { class: "filter-row" }, ...rows),
      el("label", { class: `mini-select wide${f.conv ? " active" : ""}`, title: "Conversation" }, icon("forum", "xs"), convSel),
      timeInputs());
  }
  /** from / to inputs for the shared time window */
  function timeInputs() {
    const f = W.filter;
    const inp = (key, label) => el("input", {
      type: "datetime-local", class: "text-input", "aria-label": label, title: label, value: SS.isoToLocalInput(f[key]),
      onchange: (e) => setFilter({ [key]: SS.localInputToIso(e.target.value) }),
    });
    return el("div", { class: `time-row${f.from || f.to ? " active" : ""}` },
      el("div", { class: "time-head" }, icon("schedule", "xs"), el("span", { class: "grow" }, f.from || f.to ? SS.fmtRange(f.from, f.to) : "Any time"),
        f.from || f.to ? el("button", { class: "icon-btn sm", title: "Clear the time window", onclick: () => setFilter({ from: "", to: "" }) }, icon("close", "sm")) : null),
      el("label", {}, el("span", {}, "From"), inp("from", "From (inclusive)")),
      el("label", {}, el("span", {}, "To"), inp("to", "To (exclusive)")));
  }

  /* -------------------------------------------------------- annotations */
  const { bulkTag, coverage } = M;
  async function newTag() {
    const name = await M.newTag();
    if (name) { broadcast({ type: "annotations" }); emit("annotations"); }
    return name;
  }

  /** right-click menu for a table row: filter on its host / user / agent / session / days, tags and a comment */
  const itemMenu = (ref, x, y, target) => M.itemMenu(ref, x, y, { target, onFilter: filterOn, onSessions: showSessions });
  function filterOn(patch, what) {
    const prev = { ...W.filter };
    setFilter(patch);
    snack(`Showing only ${what}`, { label: "Undo", run: () => setFilter(prev) }, 6000);
  }
  /** only these sessions, whatever the host / user / agent scope (the time window stays) */
  function showSessions(convIds, what) {
    const prev = { filter: { ...W.filter }, hidden: [...W.hiddenConvs] };
    const setHidden = (list) => { W.hiddenConvs = new Set(list); store.set("hiddenConvs", list); };
    const keep = new Set(convIds);
    setHidden(W.convOrder.filter((c) => !keep.has(c)));
    setFilter({ host: "" });
    snack(`Showing ${plural(keep.size, "session")} ${what}`, { label: "Undo", run: () => { setHidden(prev.hidden); setFilter(prev.filter); } }, 6000);
  }
  /** "Tags & comment" of a detail pane, read-only */
  function annotationSection(target) {
    const view = M.annotationView(target);
    return view ? [el("h4", {}, icon("sell", "xs"), "Tags & comment"), view] : [];
  }

  /** tag filter chips; clicking toggles the shared tag filter */
  function renderTagFilter(container, { counts = null } = {}) {
    const c = counts || (() => {
      const m = new Map();
      for (const a of Object.values(W.annotations)) for (const t of a.tags) m.set(t, (m.get(t) || 0) + 1);
      return m;
    })();
    container.replaceChildren(...W.tags.map((t) => SS.onlyChip({
      key: t.name, label: `“${t.name}”`, set: W.tagFilter, cls: `tagf${c.get(t.name) ? "" : " muted-chip"}`, style: { "--tag": t.color },
      title: `Show only items tagged “${t.name}”`,
      onChange: () => {
        store.set("tagFilter", [...W.tagFilter]);
        broadcast({ type: "tagFilter", tags: [...W.tagFilter] });
        emit("tagFilter");
      },
      children: [el("span", { class: "swatch" }), el("span", { class: "label" }, t.name), el("span", { class: "count" }, fmt(c.get(t.name) || 0))],
    })),
    el("button", {
      class: `chip sm${W.hideIgnored ? " selected" : ""}`, title: "Hide everything tagged “ignore”",
      onclick: () => { W.hideIgnored = !W.hideIgnored; store.set("hideIgnored", W.hideIgnored); emit("tagFilter"); },
    }, icon(W.hideIgnored ? "visibility_off" : "visibility", "xs"), "Hide ignored"));
  }

  /* ------------------------------------------------------------ findings */
  function findingsForNode(id) {
    const n = W.nodes.get(id);
    if (!n) return [];
    if (n.type === "entity") return W.findingsByNode.get(id) || [];
    if (n.type === "conversation") return W.findings.filter((f) => f.conv === n.conv[0]);
    if (n.type === "tool_hub") return W.findings.filter((f) => (W.nodes.get(f.event) || {}).tool === n.label);
    return W.findingsByEvent.get(n.type === "tool_arg" ? n.event : id) || [];
  }
  /** security categories as [{ key, label }] */
  const secCategories = () => Object.entries((W.data && W.data.security && W.data.security.categories) || {}).map(([key, label]) => ({ key, label }));
  const findingCard = (f, { withConv = true } = {}) => M.findingCard(f, { style: { cursor: "default" } }, null, withConv ? M.findingWhere(f) : null);

  /* ------------------------------------------------------------ text */
  /** paragraph text with <mark>s over [start, end] spans */
  function markedHTML(text, spans = []) {
    const sorted = spans.filter((s) => s[1] > s[0]).sort((a, b) => a[0] - b[0]);
    let out = "", pos = 0;
    for (const [s, e] of sorted) {
      if (s < pos) continue;
      out += esc(text.slice(pos, s)) + `<mark class="sel">${esc(text.slice(s, e))}</mark>`;
      pos = e;
    }
    return out + esc(text.slice(pos));
  }

  /* ---------------------------------------------------------- navigation */
  /** open the graph in this tab with the item selected and focused; a turn, tool argument or session also narrows
   *  the scope to its conversation (a term keeps the scope: it can span many). Back returns to this page. */
  function showInGraph({ id = null, para = null } = {}) {
    const n = id && W.nodes.get(id);
    const ev = W.events.get(id) || (para && W.events.get((W.paras.get(para) || {}).e));
    const cid = n && n.type !== "entity" && n.type !== "tool_hub" && n.conv && n.conv.length === 1 ? n.conv[0] : n ? null : ev && ev.c;
    const c = cid && W.convs.get(cid);
    location.href = SS.pageURL("/", { ...(c ? { host: c.host, user: c.user, harness: c.harness, conv: c.id } : {}), select: id, para });
  }
  /** the turn's row on the timeline, selected; the timeline page selects it in place */
  function showInTimeline(evId) {
    const key = "event:" + evId;
    if (window.SynthSiftTimeline) window.SynthSiftTimeline.select(key);
    else location.href = SS.pageURL("/timeline", { select: key });
  }

  /* ------------------------------------------------------------- sync */
  const chan = "BroadcastChannel" in window ? new BroadcastChannel("synthsift") : null;
  const WIN_ID = Math.random().toString(36).slice(2);
  function broadcast(msg) { if (chan) chan.postMessage({ ...msg, from: WIN_ID }); }
  if (chan) {
    chan.addEventListener("message", async (e) => {
      const m = e.data;
      if (!m || m.from === WIN_ID) return;
      if (m.type === "annotations") { await loadAnnotations(); emit("annotations"); }
      else if (m.type === "filters") {
        W.hiddenConvs = new Set(m.hiddenConvs || []);
        W.filter = { ...SS.emptyScope(), ...(m.filter || W.filter) };
        emit("filters");
      } else if (m.type === "tagFilter") { W.tagFilter = new Set(m.tags || []); emit("tagFilter"); }
      else if (m.type === "reload") { await load(); emit("reload"); }
    });
  }
  function on(fn) { W.listeners.add(fn); }
  function emit(what) { for (const fn of W.listeners) fn(what); }

  // reload when the server finishes a new analysis
  let lastVersion = null;
  async function watchVersion() {
    let delay = 3000;
    try {
      const st = await api("/api/status");
      SS.loading.update(st, { blocking: !W.data });
      if (st.state === "running") delay = 600;
      if (lastVersion !== null && st.state === "idle" && st.version !== lastVersion) { await load(); emit("reload"); }
      if (st.state === "idle") lastVersion = st.version;
    } catch (e) { /* offline */ }
    setTimeout(watchVersion, delay);
  }

  /* --------------------------------------------- table pages: chrome */
  /** the theme button and the filter-rail toggle (remembered under railKey) */
  function wireTopbar(railKey) {
    const btn = document.getElementById("btn-theme");
    const themeIcon = () => (btn.querySelector(".msi").textContent = SS.effectiveTheme(store.get("theme", "auto")) === "dark" ? "light_mode" : "dark_mode");
    btn.addEventListener("click", () => { SS.applyTheme(SS.effectiveTheme(store.get("theme", "auto")) === "dark" ? "light" : "dark"); themeIcon(); });
    themeIcon();
    const shell = document.getElementById("shell");
    shell.classList.toggle("rail-closed", store.get(railKey, false));
    document.getElementById("btn-rail").addEventListener("click", () => store.set(railKey, shell.classList.toggle("rail-closed")));
  }

  /** the filter rail's first section: conversation scope (see renderScope) and a "Reset all" button */
  const scopeSection = (onReset) => el("div", { class: "rail-section" },
    el("h3", {}, icon("filter_list", "xs"), "Filters", el("button", { id: "reset", onclick: onReset }, "Reset all")),
    el("div", { id: "scope" }), el("div", { id: "hidden-note" }));
  /** heading of the rail's tag section */
  const tagsHeading = () => el("h3", {}, icon("sell", "xs"), "Tags",
    el("button", { onclick: async () => { const n = await newTag(); if (n) snack(`Tag “${n}” created`); } }, "New tag"));

  /** host / user / agent / conversation selects and a note about conversations hidden in the graph */
  function renderScope() {
    renderFilterSelects(document.getElementById("scope"));
    const hidden = [...W.hiddenConvs].filter((c) => W.convs.has(c));
    document.getElementById("hidden-note").replaceChildren(...(hidden.length ? [el("div", { class: "field-row cell-muted" }, icon("visibility_off", "xs"),
      el("span", { class: "grow" }, `${plural(hidden.length, "conversation")} hidden in the graph`),
      el("button", { class: "btn text sm", onclick: () => { W.hiddenConvs.clear(); store.set("hiddenConvs", []); setFilter({}); } }, "Show"))] : []));
  }
  /** the filters shared with the graph page: conversation scope and tag filter */
  const scopeActive = () => W.tagFilter.size || SS.SCOPE_KEYS.some((k) => W.filter[k]);
  function resetScope() {
    W.tagFilter.clear(); store.set("tagFilter", []); broadcast({ type: "tagFilter", tags: [] });
    setFilter(SS.emptyScope()); // re-renders via the "filters" event
  }

  /** security category chips with counts; `selected` is the page's set of category keys */
  function renderCatChips(container, counts, selected, onChange) {
    container.replaceChildren(...secCategories().filter((c) => counts.get(c.key) || selected.has(c.key)).map((c) => SS.onlyChip({
      key: c.key, label: c.label.toLowerCase(), set: selected, title: `Only ${c.label.toLowerCase()} findings`, onChange: () => onChange(),
      children: [el("span", { class: "label" }, c.label), el("span", { class: "count" }, fmt(counts.get(c.key) || 0))],
    })));
  }

  /** rows-per-page select, "a–b of n" and page buttons */
  function pager({ n, page, pageSize, sizes, firstLast = false, onSize, goPage }) {
    const pages = Math.max(1, Math.ceil(n / pageSize));
    const lo = n ? page * pageSize + 1 : 0, hi = Math.min(n, (page + 1) * pageSize);
    const btn = (ic, title, disabled, p) => el("button", { class: "icon-btn sm", title, disabled, onclick: () => goPage(p) }, icon(ic));
    return el("div", { class: "pager" },
      el("select", { "aria-label": "Rows per page", onchange: (e) => onSize(Number(e.target.value)) },
        ...sizes.map((s) => el("option", { value: s, selected: s === pageSize }, `${s} / page`))),
      el("span", { style: { margin: "0 6px" } }, `${fmt(lo)}–${fmt(hi)} of ${fmt(n)}`),
      firstLast ? btn("first_page", "First page", page === 0, 0) : null,
      btn("chevron_left", "Previous page", page === 0, page - 1),
      btn("chevron_right", "Next page", page >= pages - 1, page + 1),
      firstLast ? btn("last_page", "Last page", page >= pages - 1, pages - 1) : null);
  }

  /** show / hide the optional columns; `hidden` is the page's set of hidden column keys */
  function columnsMenu(anchor, cols, hidden, onChange) {
    SS.menu(anchor, () => cols.filter((c) => !c.fixed).map((c) => ({
      label: c.label, checked: !hidden.has(c.key), run: () => { hidden.has(c.key) ? hidden.delete(c.key) : hidden.add(c.key); onChange(); },
    })));
  }

  /* ----------------------------------------- table pages: checked rows */
  /** the grid header's "select this page" checkbox over the page's row keys */
  function pageCheckbox(keys, checked, onChange) {
    const n = keys.filter((k) => checked.has(k)).length;
    return el("th", { class: "cb" }, el("button", {
      class: `cbx${n ? " on" : ""}`, title: n === keys.length ? "Unselect this page" : "Select this page",
      onclick: () => { const all = n === keys.length; for (const k of keys) all ? checked.delete(k) : checked.add(k); onChange(); },
    }, icon(n === 0 ? "check_box_outline_blank" : n === keys.length ? "check_box" : "indeterminate_check_box", "sm")));
  }
  const rowCheckbox = (key, on) => el("td", { class: "cb" }, el("button", { class: `cbx${on ? " on" : ""}`, "data-check": key, "aria-label": "Select row" },
    icon(on ? "check_box" : "check_box_outline_blank", "sm")));
  /** check / uncheck a row; shift-click sets the whole range from the anchor (the last row clicked) */
  function toggleCheck(checked, keys, anchor, key, shift) {
    if (shift && anchor) {
      const [a, b] = [keys.indexOf(anchor), keys.indexOf(key)].sort((x, y) => x - y);
      const on = !checked.has(key);
      if (a >= 0) for (const k of keys.slice(a, b + 1)) on ? checked.add(k) : checked.delete(k);
    } else checked.has(key) ? checked.delete(key) : checked.add(key);
  }

  /** toolbar for the checked rows: clear, select all rows (keys), and a tri-state chip per tag */
  function bulkBar({ checked, keys, canSelectAll, targets, redraw }) {
    const ts = targets();
    return el("div", { class: "bulk-bar" },
      el("button", { class: "icon-btn sm", title: "Clear selection", onclick: () => { checked.clear(); redraw(); } }, icon("close", "sm")),
      el("b", {}, `${fmt(checked.size)} selected`),
      canSelectAll ? el("button", { class: "btn text sm", onclick: () => { for (const k of keys) checked.add(k); redraw(); } }, `Select all ${fmt(keys.length)}`) : null,
      el("span", { class: "muted", style: { margin: "0 4px" } }, "Tag:"),
      ...W.tags.map((t) => {
        const s = coverage(ts, t.name);
        return el("button", {
          class: `chip sm${s.all ? " all" : ""}`, style: { "--tag": t.color }, role: "checkbox", "aria-checked": s.aria,
          title: s.all ? `Remove “${t.name}” from ${plural(ts.length, "item")}` : `Tag ${plural(ts.length, "item")} “${t.name}”`,
          onclick: () => bulkTag(ts, t.name, !s.all),
        }, icon(s.icon, "xs"), t.name);
      }),
      el("button", { class: "chip sm", onclick: async () => { const n = await newTag(); if (n) bulkTag(targets(), n, true); } }, icon("add", "xs"), "New"));
  }
  /** right-click menu for the checked rows */
  const bulkMenu = (x, y, targets, opts) => M.bulkMenu(x, y, targets, opts);
  /** a row click with a modifier: Ctrl / ⌘ checks or unchecks the row, Shift checks every row from the anchor (the
   *  last row clicked) to this one. `keys` are all the rows in order. False for a plain click. */
  function selectClick(e, checked, keys, anchor, key) {
    if (e.shiftKey && anchor && anchor !== key) {
      const [a, b] = [keys.indexOf(anchor), keys.indexOf(key)].sort((x, y) => x - y);
      if (a < 0) checked.add(key);
      else for (const k of keys.slice(a, b + 1)) checked.add(k);
      return true;
    }
    if (e.ctrlKey || e.metaKey || e.shiftKey) {
      checked.has(key) ? checked.delete(key) : checked.add(key);
      return true;
    }
    return false;
  }

  /* ------------------------------------------------------ detail views */
  /** small superscript buttons right after the title: `copy` (text to copy), `graph` and `timeline` (show it there) */
  function detailHead(icoName, color, title, chips, onClose, { copy = null, graph = null, timeline = null } = {}) {
    const sup = (ic, label, onclick) => el("button", { class: "title-sup", title: label, "aria-label": label, onclick }, icon(ic));
    return el("div", { class: "detail-head" },
      el("span", { class: "ico", style: { background: color } }, icon(icoName)),
      el("div", { class: "grow" }, el("div", { class: "title" }, title,
        copy ? sup("content_copy", "Copy", () => { navigator.clipboard && navigator.clipboard.writeText(copy); snack("Copied"); }) : null,
        graph ? sup("hub", "Show in graph", graph) : null,
        timeline ? sup("timeline", "Show in timeline", timeline) : null), el("div", { class: "sub" }, ...chips)),
      onClose ? el("button", { class: "icon-btn sm", title: "Close", onclick: onClose }, icon("close", "sm")) : null);
  }
  const tag = (text) => el("span", { class: "tag" }, text);

  /** occurrences of a node, one card per paragraph, word highlighted */
  function occurrencesEl(n, limit = 25) {
    const byPara = new Map();
    for (const [pid, s, e] of n.occ || []) {
      const p = W.paras.get(pid);
      if (!p || !paraVisible(pid)) continue;
      if (!byPara.has(pid)) byPara.set(pid, []);
      byPara.get(pid).push([s, e]);
    }
    const box = el("div");
    const pids = [...byPara.keys()];
    const render = (count) => {
      box.replaceChildren();
      for (const pid of pids.slice(0, count)) {
        const p = W.paras.get(pid);
        const ev = W.events.get(p.e);
        const c = W.convs.get(p.c);
        const whole = n.type !== "entity";
        const spans = whole ? [] : byPara.get(pid);
        const mono = p.code || p.r === "tool_call" || p.r === "tool_result";
        box.append(el("div", { class: "occ" },
          el("div", { class: "occ-head" }, el("span", { class: "dot", style: { background: c.color } }),
            el("span", { class: "t grow", title: c.title }, `${c.title} · ${ev.label}`), el("span", {}, fmtTime(ev.ts)),
            el("button", { class: "icon-btn sm", title: "Show in the graph and transcript", onclick: () => showInGraph({ id: n.id, para: pid }) }, icon("hub", "xs")),
            el("button", { class: "icon-btn sm", title: "Show in timeline", onclick: () => showInTimeline(p.e) }, icon("timeline", "xs"))),
          el("div", { class: `occ-body${mono ? " mono" : ""}`, html: markedHTML(p.t, spans) })));
      }
      if (pids.length > count) {
        box.append(el("button", { class: "btn text sm", onclick: () => render(count + 50) }, icon("expand_more"), `Show more (${fmt(pids.length - count)} remaining)`));
      }
    };
    render(limit);
    return { el: box, count: pids.length };
  }

  function nodeDetail(id, { onClose, onSelect } = {}) {
    const n = W.nodes.get(id);
    if (!n) return el("div");
    const k = kindOf(n);
    const convs = (n.conv || []).filter(convVisible).map((c) => W.convs.get(c));
    const fs = findingsForNode(id);
    const target = targetOf(id);
    const chips = [tag(k.label)];
    if (n.type === "entity") chips.push(tag(`${fmt(n.count || 0)} mentions`));
    chips.push(tag(`${fmt(n.deg || 0)} links`), tag(`${fmt(convs.length)} conversations`));
    for (const l of n.labels || []) chips.push(el("span", { class: "tag warn", title: "On an IOC / keyword list" }, icon("playlist_add_check", "xs"), l));
    const [first, last] = seenRange(id);
    const body = el("div", { class: "detail-body" });
    if (first) body.append(el("div", { class: "cell-muted" }, `Seen ${fmtTime(first)}${last && last !== first ? " – " + fmtTime(last) : ""}`));
    if (fs.length) body.append(el("h4", {}, icon("shield", "xs"), "Findings", el("span", { class: "count" }, fmt(fs.length))), ...fs.slice(0, 20).map((f) => findingCard(f)));
    body.append(...annotationSection(target));
    if (convs.length) {
      body.append(el("h4", {}, icon("forum", "xs"), "Conversations", el("span", { class: "count" }, fmt(convs.length))),
        el("div", { class: "nb-list" }, convs.slice(0, 30).map((c) => el("button", {
          class: "chip sm", style: { "--c": c.color }, title: `${c.host} / ${c.user} / ${c.harness}`,
          onclick: () => onSelect && onSelect("conv:" + c.id),
        }, el("span", { class: "swatch" }), el("span", { class: "label" }, c.title)))));
    }
    const nbs = (W.neighbours.get(id) || []).filter((x) => { const m = W.nodes.get(x.id); return m && nodeVisible(m); });
    if (nbs.length) {
      // One button per label: the same command run on every turn is a separate arg node with an identical
      // label, so look-alikes collapse into the first, with a count.
      const seen = new Set(), byLabel = new Map();
      for (const x of nbs) {
        if (seen.has(x.id)) continue;
        seen.add(x.id);
        const m = W.nodes.get(x.id);
        const key = `${kindOf(m).key}\u0000${m.label}`;
        if (byLabel.has(key)) byLabel.get(key).count++;
        else byLabel.set(key, { id: x.id, m, count: 1 });
      }
      const groups = [...byLabel.values()];
      body.append(el("h4", {}, icon("hub", "xs"), "Linked nodes", el("span", { class: "count" }, fmt(groups.length))),
        el("div", { class: "nb-list" }, groups.slice(0, 40).map(({ id, m, count }) =>
          el("button", { class: "chip sm", style: { "--c": kindOf(m).color },
            title: `${m.type}${m.label ? " · " + m.label : ""}${count > 1 ? ` · ${count} linked` : ""}`,
            onclick: () => onSelect && onSelect(id) }, el("span", { class: "swatch" }), el("span", { class: "label" }, m.label),
            count > 1 ? el("span", { class: "count" }, `×${count}`) : null))));
    }
    const occ = occurrencesEl(n);
    if (occ.count) body.append(el("h4", {}, icon("format_quote", "xs"), n.type === "entity" ? "Occurrences" : "Text", el("span", { class: "count" }, fmt(occ.count))), occ.el);
    return el("div", {}, detailHead(k.icon, n.type === "conversation" && convs[0] ? convs[0].color : k.color, n.label, chips, onClose,
      { copy: n.label, graph: () => showInGraph({ id }) }), body);
  }

  /** a turn with the turns around it */
  function turnContext(evId, { before = 1, after = 1 } = {}) {
    const ev = W.events.get(evId);
    if (!ev) return el("div");
    const list = W.data.events.filter((e) => e.c === ev.c);
    const i = list.findIndex((e) => e.id === evId);
    const box = el("div");
    for (let j = Math.max(0, i - before); j <= Math.min(list.length - 1, i + after); j++) {
      const e = list[j];
      const text = e.p.map((pid) => W.paras.get(pid).t).join("\n\n");
      const mono = e.type === "tool_call" || e.type === "tool_result";
      const worst = SS.worstSeverity(W.findingsByEvent.get(e.id) || []);
      box.append(el("div", { class: `turn${j === i ? " focus" : " ctx"}` },
        el("div", { class: "turn-head", html: `${M.avatarHTML(e.type)}<span class="who">${esc(e.label)}</span>` +
          (worst ? `<span class="sev-chip sev-${worst}">${worst}</span>` : "") + tagChipsHTML(tagsFor("event:" + e.id)) +
          `<span style="margin-left:auto">${esc(fmtTime(e.ts))}</span>` }),
        el("div", { class: `turn-text${mono ? " mono" : ""}` }, text.length > 4000 ? text.slice(0, 4000) + "…" : text)));
    }
    return box;
  }

  function turnDetail(evId, { onClose, finding = null } = {}) {
    const ev = W.events.get(evId);
    if (!ev) return el("div");
    const c = W.convs.get(ev.c);
    const k = W.kinds.get(ev.type) || { color: "#80868b" };
    const fs = W.findingsByEvent.get(evId) || [];
    const body = el("div", { class: "detail-body" });
    if (finding) body.append(findingCard(finding, { withConv: false }));
    else if (fs.length) body.append(el("h4", {}, icon("shield", "xs"), "Findings", el("span", { class: "count" }, fmt(fs.length))), ...fs.map((f) => findingCard(f, { withConv: false })));
    // "# before / # after" context, shared with the graph page's matches view
    const ctx = { before: store.get("ctxBefore", W.settings.context_before ?? 1), after: store.get("ctxAfter", W.settings.context_after ?? 1) };
    const ctxBox = el("div");
    const drawCtx = () => ctxBox.replaceChildren(turnContext(evId, ctx));
    const stepper = (key, label) => {
      const inp = el("input", { type: "number", min: 0, max: 20, value: ctx[key], "aria-label": `Turns ${label}` });
      inp.addEventListener("change", () => {
        ctx[key] = Math.max(0, Math.min(20, Number(inp.value) || 0));
        inp.value = ctx[key];
        store.set(key === "before" ? "ctxBefore" : "ctxAfter", ctx[key]);
        drawCtx();
      });
      return el("label", { class: "ctx-step" }, el("span", { class: "hash" }, "#"), label, inp);
    };
    drawCtx();
    body.append(...annotationSection("event:" + evId),
      el("h4", {}, icon("forum", "xs"), "In context", el("span", { class: "grow" }), stepper("before", "before"), stepper("after", "after")), ctxBox);
    return el("div", {}, detailHead(SS.ROLE_ICON[ev.type] || "chat", k.color, ev.label,
      [tag(`${c.host} / ${c.user}`), tag(c.title), ev.ts ? tag(fmtTime(ev.ts)) : null].filter(Boolean), onClose,
      { graph: () => showInGraph({ id: evId, para: ev.p[0] }), timeline: () => showInTimeline(evId) }), body);
  }

  function convDetail(cid, { onClose, onSelect } = {}) {
    const c = W.convs.get(cid);
    if (!c) return el("div");
    const fs = W.findings.filter((f) => f.conv === cid);
    const bySev = new Map();
    for (const f of fs) bySev.set(f.severity, (bySev.get(f.severity) || 0) + 1);
    const tagged = Object.entries(W.annotations).filter(([t, a]) => t.startsWith("event:") && a.conv === cid);
    const body = el("div", { class: "detail-body" },
      el("dl", { class: "kv" }, el("dt", {}, "Host"), el("dd", {}, c.host), el("dt", {}, "User"), el("dd", {}, c.user),
        el("dt", {}, "Agent"), el("dd", {}, c.harness), el("dt", {}, "Model"), el("dd", {}, c.model || "–"),
        el("dt", {}, "File"), el("dd", {}, c.source), el("dt", {}, "Started"), el("dd", {}, fmtTime(c.started_at) || "–"),
        el("dt", {}, "Size"), el("dd", {}, `${fmt(c.n_events)} steps · ${fmt(c.n_tool_calls)} tool calls · ${fmt(c.n_thoughts)} thoughts`),
        ...Object.entries(c.meta || {}).filter(([k]) => !["session_id", "format_version", "user_type"].includes(k))
          .flatMap(([k, v]) => [el("dt", {}, k.replace(/_/g, " ")), el("dd", {}, String(v))])),
      fs.length ? el("div", { class: "sec-summary", style: { marginTop: "12px" } }, [...SS.SEV_ORDER].reverse().filter((s) => bySev.get(s))
        .map((s) => el("span", { class: `chip sm sev-${s}` }, el("span", { class: "sev-chip" }, s), el("span", { class: "count" }, fmt(bySev.get(s)))))) : null,
      ...annotationSection("conv:" + cid));
    if (tagged.length) {
      body.append(el("h4", {}, icon("chat", "xs"), "Tagged turns", el("span", { class: "count" }, fmt(tagged.length))),
        el("div", { class: "nb-list" }, tagged.map(([t, a]) => el("button", { class: "chip sm", onclick: () => onSelect && onSelect(t) },
          el("span", { class: "label" }, a.label || t), el("span", { class: "tag-row", html: tagChipsHTML(a.tags) })))));
    }
    return el("div", {}, detailHead("forum", c.color, c.title, [tag("session"), ...SS.metaChips(c.meta).slice(0, 3)], onClose,
      { graph: () => showInGraph({ id: "conv:" + cid }) }), body);
  }

  return {
    W, load, on, watchVersion, setFilter, scopeFromURL, broadcast,
    kindOf, kindGroup: M.kindGroup, convVisible, nodeVisible, inWindow: M.inWindow, windowOn: M.windowOn,
    eventVisible: M.eventVisible, paraVisible,
    targetOf, annOf, tagsFor, nodeTags: M.nodeTags, labelFor, convFor: M.convFor, tsFor: M.tsFor, seenRange,
    tagChipsHTML, itemMenu, annotationSection, renderTagFilter,
    findingsForNode, chainEl, endpointLabel: M.endpointLabel, showInGraph, showInTimeline,
    wireTopbar, scopeSection, tagsHeading, renderScope, scopeActive, resetScope, renderCatChips, pager, columnsMenu,
    pageCheckbox, rowCheckbox, toggleCheck, selectClick, bulkBar, bulkMenu, sharedNodes: M.sharedNodes,
    nodeDetail, turnDetail, convDetail,
  };
})();
