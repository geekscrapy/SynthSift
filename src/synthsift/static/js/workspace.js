/* Shared workspace model for the full-page views (Nodes, Timeline).
 *
 * Loads the analysed graph, settings and analyst annotations, and exposes the
 * same filter / tag / comment semantics as the graph page. Filters and tags use
 * the same localStorage keys and BroadcastChannel messages as the graph page, so
 * every open page stays in sync.
 */
"use strict";

const WS = (() => {
  const { store, api, esc, el, icon, snack, fmt } = SS;

  const SEV_ORDER = ["info", "low", "medium", "high", "critical"];
  const SEV_COLOR = { critical: "#A50E0E", high: "#D93025", medium: "#E37400", low: "#B08800", info: "#5F6368" };
  const sevRank = (s) => SEV_ORDER.indexOf(s);
  const STRUCTURAL = new Set(["conversation", "user", "assistant", "system", "thought", "tool_call", "tool_arg", "tool_result", "tool_hub"]);
  const LAYERS = [
    { key: "thought", label: "Thoughts", icon: "psychology" },
    { key: "dialogue", label: "Dialogue", icon: "forum" },
    { key: "action", label: "Actions", icon: "build" },
    { key: "entity", label: "Entities", icon: "hub" },
  ];
  const EXTRA_COLORS = ["#7B1FA2", "#00897B", "#C0CA33", "#6D4C41", "#3949AB", "#D81B60", "#00ACC1", "#F4511E"];

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
    filter: store.get("convFilter", { host: "", user: "", harness: "", conv: "" }),
    hiddenConvs: new Set(store.get("hiddenConvs", [])),
    tagFilter: new Set(store.get("tagFilter", [])),
    hideIgnored: store.get("hideIgnored", true),
    listeners: new Set(),
  };

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
    W.findings = []; W.findingsByEvent.clear(); W.convOrder = [];
    if (!W.data) return;
    for (const k of [...g.kinds.node_types, ...g.kinds.categories]) W.kinds.set(k.key, { ...k, color: W.settings["color." + k.key] || k.color });
    W.convOrder = g.conversations.map((c) => c.id);
    for (const c of g.conversations) W.convs.set(c.id, c);
    for (const e of g.events) W.events.set(e.id, e);
    for (const p of g.paragraphs) W.paras.set(p.id, p);
    for (const n of g.nodes) W.nodes.set(n.id, n);
    W.findings = (g.findings || []).map((f, i) => ({ ...f, i }));
    // findings per event and per entity node (resolved the way the graph builder does)
    W.findingsByNode = new Map();
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

  async function loadAnnotations() {
    try {
      const r = await api("/api/annotations");
      W.annotations = r.annotations || {};
      W.tags = r.tags || [];
    } catch (e) { /* keep previous */ }
  }

  /* -------------------------------------------------------------- kinds */
  const kindKey = (n) => (n.type === "entity" ? n.category : n.type);
  function kindOf(n) {
    const key = kindKey(n);
    let k = W.kinds.get(key);
    if (!k) {
      let h = 0;
      for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
      k = { key, label: key.replace(/_/g, " "), group: "Custom", icon: "label", color: EXTRA_COLORS[h % EXTRA_COLORS.length] };
      W.kinds.set(key, k);
    }
    return k;
  }
  const kindGroup = (key) => (STRUCTURAL.has(key) ? "Conversation structure" : (W.kinds.get(key) || {}).group || "Other");

  /* ------------------------------------------------------------ filters */
  function convMatchesFilter(cid) {
    const c = W.convs.get(cid);
    if (!c) return false;
    const f = W.filter;
    return (!f.host || c.host === f.host) && (!f.user || c.user === f.user) && (!f.harness || c.harness === f.harness)
      && (!f.conv || c.id === f.conv);
  }
  const convVisible = (cid) => !W.hiddenConvs.has(cid) && convMatchesFilter(cid);
  const nodeVisible = (n) => !(n.conv && n.conv.length) || n.conv.some(convVisible);

  function setFilter(patch, { broadcastIt = true } = {}) {
    W.filter = { ...W.filter, ...patch };
    if ("host" in patch) { W.filter.user = patch.user || ""; W.filter.harness = patch.harness || ""; }
    else if ("user" in patch) W.filter.harness = patch.harness || "";
    if (("host" in patch || "user" in patch || "harness" in patch) && !("conv" in patch)) W.filter.conv = "";
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
      el("label", { class: `mini-select wide${f.conv ? " active" : ""}`, title: "Conversation" }, icon("forum", "xs"), convSel));
  }

  /* -------------------------------------------------------- annotations */
  function targetOf(nodeId) {
    const n = W.nodes.get(nodeId);
    if (!n) return null;
    if (n.type === "conversation") return nodeId;
    if (n.type === "entity") return "term:" + nodeId;
    if (n.type === "tool_arg") return "event:" + n.event;
    if (n.type === "tool_hub") return null;
    return "event:" + nodeId;
  }
  const annOf = (t) => (t && W.annotations[t]) || null;
  const tagsFor = (t) => (annOf(t) || {}).tags || [];
  const tagInfo = (name) => W.tags.find((t) => t.name === name) || { name, color: "#5F6368", icon: "sell" };
  function nodeTags(nodeId) {
    const own = tagsFor(targetOf(nodeId));
    const n = W.nodes.get(nodeId);
    if (n && n.type !== "entity" && n.type !== "conversation" && n.conv && n.conv[0]) {
      const ct = tagsFor("conv:" + n.conv[0]);
      if (ct.length) return [...new Set([...own, ...ct])];
    }
    return own;
  }

  function labelFor(target) {
    if (target.startsWith("conv:")) { const c = W.convs.get(target.slice(5)); return c ? c.title : target; }
    if (target.startsWith("event:")) {
      const ev = W.events.get(target.slice(6));
      if (!ev) return target;
      const first = ev.p.length ? W.paras.get(ev.p[0]).t.replace(/\s+/g, " ").slice(0, 90) : "";
      return `${ev.label}${first ? " – " + first : ""}`;
    }
    const n = W.nodes.get(target.slice(5));
    return n ? n.label : target.slice(5).replace(/^ent:/, "");
  }
  function convFor(target) {
    if (target.startsWith("conv:")) return target.slice(5);
    if (target.startsWith("event:")) { const ev = W.events.get(target.slice(6)); return ev ? ev.c : null; }
    const n = W.nodes.get(target.slice(5));
    return n && n.conv && n.conv.length ? n.conv[0] : null;
  }
  function tsFor(target) {
    if (target.startsWith("conv:")) { const c = W.convs.get(target.slice(5)); return c ? c.started_at || null : null; }
    if (target.startsWith("event:")) { const ev = W.events.get(target.slice(6)); return ev ? ev.ts || null : null; }
    return seenRange(target.slice(5))[0];
  }
  /** earliest / latest timestamp a node appears at */
  function seenRange(nodeId) {
    const n = W.nodes.get(nodeId);
    let lo = null, hi = null;
    for (const [pid] of (n && n.occ) || []) {
      const ts = W.events.get(W.paras.get(pid)?.e)?.ts;
      if (!ts) continue;
      if (!lo || ts < lo) lo = ts;
      if (!hi || ts > hi) hi = ts;
    }
    return [lo, hi];
  }

  async function saveAnnotation(target, tags, comment) {
    const prev = annOf(target);
    const body = { target, tags, comment: comment ?? (prev ? prev.comment : ""), label: labelFor(target), conv: convFor(target), ts: tsFor(target) };
    try {
      const r = await api("/api/annotations", { method: "PUT", body });
      if (r.annotation) W.annotations[target] = r.annotation; else delete W.annotations[target];
      W.tags = r.tags || W.tags;
      broadcast({ type: "annotations" });
      emit("annotations");
    } catch (e) {
      snack("Could not save: " + e.message);
    }
  }
  function toggleTag(target, tag) {
    const cur = new Set(tagsFor(target));
    cur.has(tag) ? cur.delete(tag) : cur.add(tag);
    return saveAnnotation(target, [...cur]);
  }
  /** add or remove one tag on many targets at once */
  async function bulkTag(targets, tag, on) {
    for (const t of targets) {
      const cur = new Set(tagsFor(t));
      if (on === cur.has(tag)) continue;
      on ? cur.add(tag) : cur.delete(tag);
      const prev = annOf(t);
      const body = { target: t, tags: [...cur], comment: prev ? prev.comment : "", label: labelFor(t), conv: convFor(t), ts: tsFor(t) };
      try {
        const r = await api("/api/annotations", { method: "PUT", body });
        if (r.annotation) W.annotations[t] = r.annotation; else delete W.annotations[t];
        W.tags = r.tags || W.tags;
      } catch (e) { snack("Could not save: " + e.message); break; }
    }
    broadcast({ type: "annotations" });
    emit("annotations");
  }
  async function newTag() {
    const name = (prompt("New tag name") || "").trim().toLowerCase();
    if (!name) return null;
    const color = ["#1A73E8", "#9334E6", "#12B5CB", "#E52592", "#188038", "#B06000"][W.tags.length % 6];
    try {
      const r = await api("/api/tags", { method: "POST", body: { name, color } });
      W.tags = r.tags;
      broadcast({ type: "annotations" });
      emit("annotations");
      return name;
    } catch (e) { snack(e.message); return null; }
  }

  const tagChipsHTML = (tags, inherited = null) => tags.map((t) => (inherited && inherited.has(t)
    ? `<span class="tag-chip inherited" style="--tag:${esc(tagInfo(t).color)}" title="Inherited from the session">${esc(t)}</span>`
    : `<span class="tag-chip" style="--tag:${esc(tagInfo(t).color)}">${esc(t)}</span>`)).join("");

  /* ----------------------------------------------------------- tag menu */
  function closeMenus() { document.querySelectorAll(".menu").forEach((m) => m.remove()); }
  function placeMenu(menu, x, y) {
    document.body.append(menu);
    const w = menu.offsetWidth, h = menu.offsetHeight;
    menu.style.left = Math.max(8, Math.min(x, window.innerWidth - w - 8)) + "px";
    menu.style.top = Math.max(8, Math.min(y, window.innerHeight - h - 8)) + "px";
    setTimeout(() => {
      const close = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("mousedown", close, true); } };
      document.addEventListener("mousedown", close, true);
    }, 0);
  }
  const KIND_LABEL = { conv: "Session", event: "Turn", term: "Term" };
  function openTagMenu(target, x, y) {
    if (!target) return;
    closeMenus();
    const kind = target.split(":", 1)[0];
    const menu = el("div", { class: "menu tag-menu", role: "menu" });
    const render = () => {
      const cur = new Set(tagsFor(target));
      const a = annOf(target);
      const items = W.tags.map((t) => el("button", {
        role: "menuitemcheckbox", "aria-checked": cur.has(t.name) ? "true" : "false",
        onclick: async () => { await toggleTag(target, t.name); render(); },
      }, icon(cur.has(t.name) ? "check_box" : "check_box_outline_blank"), el("span", { class: "dot", style: { background: t.color } }),
        el("span", { class: "grow" }, t.name)));
      const ta = el("textarea", { class: "text-input", placeholder: "Analyst comment…" });
      ta.value = a ? a.comment : "";
      menu.replaceChildren(
        el("div", { class: "tm-head" }, `Tag ${KIND_LABEL[kind] || kind}`, el("b", { title: labelFor(target) }, labelFor(target))),
        ...items,
        el("button", { onclick: async () => { const n = await newTag(); if (n) { await toggleTag(target, n); render(); } } }, icon("add"), el("span", { class: "grow" }, "New tag…")),
        el("div", { class: "tm-comment" }, ta, el("div", { class: "tm-actions" },
          el("button", { class: "btn text sm", onclick: async () => { await saveAnnotation(target, [], ""); menu.remove(); } }, "Clear all"),
          el("button", { class: "btn filled sm", onclick: async () => { await saveAnnotation(target, [...tagsFor(target)], ta.value); menu.remove(); snack("Comment saved"); } }, "Save comment"))));
    };
    render();
    placeMenu(menu, x, y);
  }

  /** tag checkboxes + comment box for a detail pane */
  function tagEditor(target) {
    const cur = new Set(tagsFor(target));
    const chips = el("div", { class: "chip-row sel-tags" }, W.tags.map((t) => el("button", {
      class: `chip sm${cur.has(t.name) ? " on" : ""}`, style: { "--tag": t.color }, role: "checkbox",
      "aria-checked": cur.has(t.name) ? "true" : "false", onclick: () => toggleTag(target, t.name),
    }, icon(cur.has(t.name) ? "check_box" : "check_box_outline_blank"), t.name)),
      el("button", { class: "chip sm", onclick: async () => { const n = await newTag(); if (n) toggleTag(target, n); } }, icon("add"), "New tag"));
    const ta = el("textarea", { class: "text-input sel-comment", placeholder: "Analyst comment (saved when you leave the box)…" });
    ta.value = (annOf(target) || {}).comment || "";
    ta.addEventListener("change", () => saveAnnotation(target, [...tagsFor(target)], ta.value));
    return el("div", {}, chips, ta);
  }

  /** tag filter chips; clicking toggles the shared tag filter */
  function renderTagFilter(container, { counts = null } = {}) {
    const c = counts || (() => {
      const m = new Map();
      for (const a of Object.values(W.annotations)) for (const t of a.tags) m.set(t, (m.get(t) || 0) + 1);
      return m;
    })();
    container.replaceChildren(...W.tags.map((t) => el("button", {
      class: `chip sm tagf${W.tagFilter.has(t.name) ? " selected" : ""}${c.get(t.name) ? "" : " muted-chip"}`, style: { "--tag": t.color },
      onclick: () => {
        W.tagFilter.has(t.name) ? W.tagFilter.delete(t.name) : W.tagFilter.add(t.name);
        store.set("tagFilter", [...W.tagFilter]);
        broadcast({ type: "tagFilter", tags: [...W.tagFilter] });
        emit("tagFilter");
      },
    }, el("span", { class: "swatch" }), el("span", { class: "label" }, t.name), el("span", { class: "count" }, fmt(c.get(t.name) || 0)))),
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
  const worstSeverity = (fs) => fs.reduce((a, f) => (sevRank(f.severity) > sevRank(a) ? f.severity : a), "");
  function endpointLabel(key) {
    const n = W.nodes.get("ent:" + key);
    if (n) return n.label;
    const ev = W.events.get(key);
    return ev ? ev.label : key;
  }
  function chainEl(chain) {
    const box = el("div", { class: "chain" });
    chain.forEach(([src, action, dst], i) => {
      if (i === 0) box.append(el("span", { class: "node", title: endpointLabel(src) }, endpointLabel(src)));
      box.append(el("span", { class: "arrow" }, action, icon("arrow_forward")));
      box.append(el("span", { class: "node", title: endpointLabel(dst) }, endpointLabel(dst)));
    });
    return box;
  }

  /* ------------------------------------------------------------ text */
  function fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    return isNaN(d) ? String(ts) : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function fmtDay(ts) {
    const d = new Date(ts);
    return isNaN(d) ? "Unknown date" : d.toLocaleDateString([], { weekday: "short", year: "numeric", month: "short", day: "numeric" });
  }
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
  const ROLE_ICON = { user: "person", assistant: "smart_toy", system: "settings", thought: "psychology", tool_call: "build", tool_result: "output" };
  function avatarHTML(type) {
    const k = W.kinds.get(type) || {};
    return `<span class="avatar" style="background:${esc(k.color || "#80868b")}"><span class="msi">${ROLE_ICON[type] || "chat"}</span></span>`;
  }

  /* ---------------------------------------------------------- navigation */
  // Show something in the graph: reuse an open graph tab if one answers, else open one.
  function showInGraph({ id = null, para = null } = {}) {
    let done = false;
    const finish = (answered) => {
      if (done) return;
      done = true;
      if (chan) chan.removeEventListener("message", onPong);
      if (answered) {
        broadcast({ type: "reveal", id, para });
        snack("Shown in the graph tab");
      } else {
        const q = new URLSearchParams();
        if (id) q.set("select", id);
        if (para) q.set("para", para);
        window.open("/?" + q.toString(), "synthsift-graph");
      }
    };
    const onPong = (e) => { if (e.data && e.data.type === "pong") finish(true); };
    if (chan) chan.addEventListener("message", onPong);
    broadcast({ type: "ping" });
    setTimeout(() => finish(false), chan ? 900 : 0); // a graph busy laying out can take a moment to answer
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
        W.filter = { conv: "", ...(m.filter || W.filter) };
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

  /** CSV download */
  function downloadCSV(name, header, rows) {
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const text = [header.map(q).join(","), ...rows.map((r) => r.map(q).join(","))].join("\n");
    const a = el("a", { href: URL.createObjectURL(new Blob([text], { type: "text/csv" })), download: name });
    document.body.append(a); a.click(); a.remove();
  }

  function makeRegex(q) {
    const m = q.match(/^\/(.+)\/([a-z]*)$/);
    try {
      if (m) return new RegExp(m[1], m[2].replace("g", ""));
      return q ? new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i") : null;
    } catch (e) {
      return null;
    }
  }

  /* ------------------------------------------------------ detail views */
  function detailHead(icoName, color, title, chips, onClose) {
    return el("div", { class: "detail-head" },
      el("span", { class: "ico", style: { background: color } }, icon(icoName)),
      el("div", { class: "grow" }, el("div", { class: "title" }, title), el("div", { class: "sub" }, ...chips)),
      onClose ? el("button", { class: "icon-btn sm", title: "Close", onclick: onClose }, icon("close", "sm")) : null);
  }
  const tag = (text) => el("span", { class: "tag" }, text);

  function findingCard(f, { withConv = true } = {}) {
    const c = W.convs.get(f.conv);
    const ev = W.events.get(f.event);
    return el("div", { class: `finding sev-${f.severity}`, style: { cursor: "default" } },
      el("span", { class: "sev-chip" }, f.severity),
      el("span", { class: "f-title" }, f.label),
      el("span", { class: "f-detail" }, f.detail),
      f.chain.length ? chainEl(f.chain) : null,
      withConv ? el("div", { class: "f-meta" }, c ? `${c.host} / ${c.user} · ${c.title}` : "", ev ? ` · ${ev.label}` : "", ev && ev.ts ? ` · ${fmtTime(ev.ts)}` : "") : null);
  }

  /** occurrences of a node, one card per paragraph, word highlighted */
  function occurrencesEl(n, limit = 25) {
    const byPara = new Map();
    for (const [pid, s, e] of n.occ || []) {
      const p = W.paras.get(pid);
      if (!p || !convVisible(p.c)) continue;
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
            el("button", { class: "icon-btn sm", title: "Show in the graph and transcript", onclick: () => showInGraph({ id: n.id, para: pid }) }, icon("open_in_new", "xs"))),
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
    if (target) body.append(el("h4", {}, icon("sell", "xs"), "Tags & comment"), tagEditor(target));
    body.append(el("div", { class: "detail-actions" },
      el("button", { class: "btn tonal sm", onclick: () => showInGraph({ id }) }, icon("hub"), "Show in graph"),
      el("button", { class: "btn text sm", onclick: () => { navigator.clipboard && navigator.clipboard.writeText(n.label); snack("Copied"); } }, icon("content_copy"), "Copy")));
    if (convs.length) {
      body.append(el("h4", {}, icon("forum", "xs"), "Conversations", el("span", { class: "count" }, fmt(convs.length))),
        el("div", { class: "nb-list" }, convs.slice(0, 30).map((c) => el("button", {
          class: "chip sm", style: { "--c": c.color }, title: `${c.host} / ${c.user} / ${c.harness}`,
          onclick: () => onSelect && onSelect("conv:" + c.id),
        }, el("span", { class: "swatch" }), el("span", { class: "label" }, c.title)))));
    }
    const nbs = (W.neighbours.get(id) || []).filter((x) => { const m = W.nodes.get(x.id); return m && nodeVisible(m); });
    if (nbs.length) {
      const seen = new Set();
      const uniq = nbs.filter((x) => (seen.has(x.id) ? false : seen.add(x.id)));
      body.append(el("h4", {}, icon("hub", "xs"), "Linked nodes", el("span", { class: "count" }, fmt(uniq.length))),
        el("div", { class: "nb-list" }, uniq.slice(0, 40).map((x) => {
          const m = W.nodes.get(x.id);
          return el("button", { class: "chip sm", style: { "--c": kindOf(m).color }, title: `${x.type}${x.label ? " · " + x.label : ""}`,
            onclick: () => onSelect && onSelect(x.id) }, el("span", { class: "swatch" }), el("span", { class: "label" }, m.label));
        })));
    }
    const occ = occurrencesEl(n);
    if (occ.count) body.append(el("h4", {}, icon("format_quote", "xs"), n.type === "entity" ? "Occurrences" : "Text", el("span", { class: "count" }, fmt(occ.count))), occ.el);
    return el("div", {}, detailHead(k.icon, n.type === "conversation" && convs[0] ? convs[0].color : k.color, n.label, chips, onClose), body);
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
      const fs = W.findingsByEvent.get(e.id) || [];
      const worst = worstSeverity(fs);
      box.append(el("div", { class: `turn${j === i ? " focus" : " ctx"}` },
        el("div", { class: "turn-head", html: `${avatarHTML(e.type)}<span class="who">${esc(e.label)}</span>` +
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
    body.append(el("h4", {}, icon("sell", "xs"), "Tags & comment"), tagEditor("event:" + evId),
      el("div", { class: "detail-actions" },
        el("button", { class: "btn tonal sm", onclick: () => showInGraph({ id: evId, para: ev.p[0] }) }, icon("hub"), "Show in graph")),
      el("h4", {}, icon("forum", "xs"), "In context", el("span", { class: "grow" }), stepper("before", "before"), stepper("after", "after")), ctxBox);
    return el("div", {}, detailHead(ROLE_ICON[ev.type] || "chat", k.color, ev.label,
      [tag(`${c.host} / ${c.user}`), tag(c.title), ev.ts ? tag(fmtTime(ev.ts)) : null].filter(Boolean), onClose), body);
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
      fs.length ? el("div", { class: "sec-summary", style: { marginTop: "12px" } }, [...SEV_ORDER].reverse().filter((s) => bySev.get(s))
        .map((s) => el("span", { class: `chip sm sev-${s}` }, el("span", { class: "sev-chip" }, s), el("span", { class: "count" }, fmt(bySev.get(s)))))) : null,
      el("h4", {}, icon("sell", "xs"), "Tags & comment"), tagEditor("conv:" + cid),
      el("div", { class: "detail-actions" }, el("button", { class: "btn tonal sm", onclick: () => showInGraph({ id: "conv:" + cid }) }, icon("hub"), "Show in graph")));
    if (tagged.length) {
      body.append(el("h4", {}, icon("chat", "xs"), "Tagged turns", el("span", { class: "count" }, fmt(tagged.length))),
        el("div", { class: "nb-list" }, tagged.map(([t, a]) => el("button", { class: "chip sm", onclick: () => onSelect && onSelect(t) },
          el("span", { class: "label" }, a.label || t), el("span", { class: "tag-row", html: tagChipsHTML(a.tags) })))));
    }
    return el("div", {}, detailHead("forum", c.color, c.title, [tag("session"), ...SS.metaChips(c.meta).slice(0, 3)], onClose), body);
  }

  return {
    W, load, loadAnnotations, on, emit, broadcast, watchVersion,
    SEV_ORDER, SEV_COLOR, sevRank, STRUCTURAL, LAYERS,
    kindKey, kindOf, kindGroup, convVisible, nodeVisible, setFilter, renderFilterSelects,
    targetOf, annOf, tagsFor, tagInfo, nodeTags, labelFor, convFor, tsFor, seenRange,
    saveAnnotation, toggleTag, bulkTag, newTag, tagChipsHTML, openTagMenu, closeMenus, placeMenu, tagEditor, renderTagFilter,
    findingsForNode, worstSeverity, secCategories, chainEl, endpointLabel,
    fmtTime, fmtDay, markedHTML, avatarHTML, showInGraph, downloadCSV, makeRegex,
    nodeDetail, turnDetail, convDetail, turnContext, findingCard,
  };
})();
