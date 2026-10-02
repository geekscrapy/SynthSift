/* Shared workspace model for the full-page views (Nodes, Timeline, Dashboard).
 *
 * Loads the analysed graph, settings and analyst annotations, and exposes the
 * same filter / tag / comment semantics as the graph page (the lookups and tag
 * helpers are SS.model, the rail sections, filters, legends and detail headers
 * SS.panels, both shared with it). Filters and tags use the same
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
    annotations: {},
    tags: [],
    filter: { ...SS.emptyScope(), ...store.get("convFilter", {}) },
    hiddenConvs: new Set(store.get("hiddenConvs", [])),
    tagFilter: new Set(store.get("tagFilter", [])),
    hideIgnored: store.get("hideIgnored", true),
    listeners: new Set(),
  };
  const M = SS.model(W, { onSaved: () => { broadcast({ type: "annotations" }); emit("annotations"); }, promptTag: newTag });
  const { kindOf, convVisible, paraVisible, targetOf, annOf, tagsFor, labelFor, loadAnnotations, tagChipsHTML, chainEl, findingsForNode, findingsForEvent } = M;
  // the panel pieces every view shares (sections, scope, tag filter, legends, detail headers)
  const P = SS.panels(W, M, {
    setFilter: (patch) => setFilter(patch),
    onTagFilter: () => { broadcast({ type: "tagFilter", tags: [...W.tagFilter] }); emit("tagFilter"); },
    afterTagsChanged: () => { broadcast({ type: "annotations" }); emit("annotations"); },
    newTag: () => newTag(),
  });

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
    W.findings = []; W.convOrder = [];
    if (!W.data) return;
    SS.loadKinds(W.kinds, g, W.settings);
    W.convOrder = g.conversations.map((c) => c.id);
    for (const c of g.conversations) W.convs.set(c.id, c);
    for (const e of g.events) W.events.set(e.id, e);
    for (const p of g.paragraphs) W.paras.set(p.id, p);
    for (const n of g.nodes) W.nodes.set(n.id, n);
    W.findings = M.findings(); // indexed per turn and node by the model
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

  /* -------------------------------------------------------- annotations */

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
  const scopeSection = (onReset) => P.section({ icon: "filter_list", title: "Filters", actions: [{ label: "Reset all", id: "reset", run: onReset }],
    body: [el("div", { id: "scope" }), el("div", { id: "hidden-note" })] });
  /** the rail's tag section: the tag filter chips (rendered by renderScope) plus extra rows */
  const tagsSection = (...extra) => P.section({ icon: "sell", title: "Tags", countId: "tag-count", actions: P.tagActions(),
    body: [el("div", { class: "chip-row", id: "tagf" }), ...extra] });

  /** host / user / agent / conversation selects, the time window, a note about conversations hidden in the graph,
   *  and the tag filter */
  function renderScope() {
    P.scope(document.getElementById("scope"));
    const tf = document.getElementById("tagf");
    if (tf) P.tagFilter(tf);
    const tc = document.getElementById("tag-count");
    if (tc) tc.textContent = fmt(Object.keys(W.annotations).length);
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
    return el("div", { class: "bulk-bar" },
      el("button", { class: "icon-btn sm", title: "Clear selection", onclick: () => { checked.clear(); redraw(); } }, icon("close", "sm")),
      el("b", {}, `${fmt(checked.size)} selected`),
      canSelectAll ? el("button", { class: "btn text sm", onclick: () => { for (const k of keys) checked.add(k); redraw(); } }, `Select all ${fmt(keys.length)}`) : null,
      el("span", { class: "muted", style: { margin: "0 4px" } }, "Tag:"),
      ...P.tagCoverage(targets));
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
  const { tag } = P;

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
    const target = targetOf(id);
    const body = el("div", { class: "detail-body" }, P.seenLine(id), ...P.findingsSection(findingsForNode(id)), ...P.annotationSection(target));
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
    return el("div", {}, P.head({ icon: k.icon, color: P.nodeColor(n), title: n.label, onClose, copy: n.label, graph: () => showInGraph({ id }),
      chips: P.nodeChips(n, { links: n.deg || 0, convs: convs.length }) }), body);
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
      const worst = SS.worstSeverity(findingsForEvent(e.id));
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
    const body = el("div", { class: "detail-body" }, ...P.findingsSection(finding ? [finding] : findingsForEvent(evId), { withConv: false, limit: 50 }));
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
    body.append(...P.annotationSection("event:" + evId),
      el("h4", {}, icon("forum", "xs"), "In context", el("span", { class: "grow" }), stepper("before", "before"), stepper("after", "after")), ctxBox);
    return el("div", {}, P.head({ icon: SS.ROLE_ICON[ev.type] || "chat", color: k.color, title: ev.label, onClose,
      chips: [tag(`${c.host} / ${c.user}`), tag(c.title), ev.ts ? tag(fmtTime(ev.ts)) : null],
      graph: () => showInGraph({ id: evId, para: ev.p[0] }), timeline: () => showInTimeline(evId) }), body);
  }

  function convDetail(cid, { onClose, onSelect } = {}) {
    const c = W.convs.get(cid);
    if (!c) return el("div");
    const fs = W.findings.filter((f) => f.conv === cid);
    const tagged = Object.entries(W.annotations).filter(([t, a]) => t.startsWith("event:") && a.conv === cid);
    const body = el("div", { class: "detail-body" }, P.convFacts(c),
      fs.length ? el("div", { style: { marginTop: "12px" } }, P.sevChips(P.findingCounts(fs).sev)) : null,
      ...P.annotationSection("conv:" + cid));
    if (tagged.length) {
      body.append(el("h4", {}, icon("chat", "xs"), "Tagged turns", el("span", { class: "count" }, fmt(tagged.length))),
        el("div", { class: "nb-list" }, tagged.map(([t, a]) => el("button", { class: "chip sm", onclick: () => onSelect && onSelect(t) },
          el("span", { class: "label" }, a.label || t), el("span", { class: "tag-row", html: tagChipsHTML(a.tags) })))));
    }
    return el("div", {}, P.head({ icon: "forum", color: c.color, title: c.title, onClose, chips: [tag("session"), ...SS.metaChips(c.meta).slice(0, 3)],
      graph: () => showInGraph({ id: "conv:" + cid }) }), body);
  }

  return {
    W, load, on, watchVersion, setFilter, scopeFromURL, broadcast,
    kindOf, kindGroup: M.kindGroup, convVisible, nodeVisible, inWindow: M.inWindow, windowOn: M.windowOn,
    eventVisible: M.eventVisible, paraVisible,
    targetOf, annOf, tagsFor, nodeTags: M.nodeTags, labelFor, convFor: M.convFor, tsFor: M.tsFor, seenRange: M.seenRange,
    tagChipsHTML, itemMenu, P,
    findingsForNode, findingsForEvent, chainEl, endpointLabel: M.endpointLabel, showInGraph, showInTimeline,
    wireTopbar, scopeSection, tagsSection, renderScope, scopeActive, resetScope, pager, columnsMenu,
    pageCheckbox, rowCheckbox, toggleCheck, selectClick, bulkBar, bulkMenu, sharedNodes: M.sharedNodes,
    nodeDetail, turnDetail, convDetail,
  };
})();
