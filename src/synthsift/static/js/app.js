/* SynthSift graph page */
"use strict";

(() => {
  const { store, api, esc, el, icon, snack, debounce, fmt, plural } = SS;
  const $ = (id) => document.getElementById(id);

  const LAYERS = [
    { key: "thought", label: "Thoughts", icon: "psychology" },
    { key: "dialogue", label: "Dialogue", icon: "forum" },
    { key: "action", label: "Actions", icon: "build" },
    { key: "entity", label: "Entities", icon: "hub" },
  ];
  const STRUCTURAL = new Set(["conversation", "user", "assistant", "system", "thought", "tool_call", "tool_arg", "tool_result", "tool_hub"]);

  const S = {
    data: null,
    version: -1,
    settings: {},
    kinds: new Map(),
    paras: new Map(),
    events: new Map(),
    convs: new Map(),
    convOrder: [],
    convParas: new Map(),
    paraPos: new Map(),
    nodes: new Map(),
    paraEnts: new Map(),
    edgesByNode: new Map(),
    hiddenConvs: new Set(store.get("hiddenConvs", [])),
    hiddenKinds: new Set(store.get("hiddenKinds", [])),
    hiddenLayers: new Set(store.get("hiddenLayers", [])),
    visibleNodes: new Set(),
    selected: null,
    neighbors: null,
    search: null,
    convSearch: null,
    matchSource: null,
    tab: "transcript",
    currentConv: null,
    labels: store.get("labels", true),
    underline: store.get("underline", null),
    physics: false,
    layout: null,
    ctxBefore: null,
    ctxAfter: null,
    collapsed: new Set(store.get("collapsedTree", [])),
    pointer: { x: 0, y: 0 },
    textCache: new Map(),
    theme: "light",
  };

  let network = null;
  let nodesDS = null;
  let edgesDS = null;
  let nodesView = null;
  let edgesView = null;

  /* ================================================================ boot */
  async function boot() {
    wireUI();
    try {
      const st = await api("/api/settings");
      S.settings = st.values;
    } catch (e) {
      snack("Could not load settings: " + e.message);
    }
    applyViewSettings();
    await refresh();
    pollStatus();
    document.fonts && document.fonts.ready.then(() => network && network.redraw());
  }

  function applyViewSettings() {
    const s = S.settings;
    S.theme = SS.applyTheme(store.get("theme", s.theme || "auto"));
    $("btn-theme").querySelector(".msi").textContent = S.theme === "dark" ? "light_mode" : "dark_mode";
    if (S.layout === null) S.layout = s.layout || "force";
    if (S.ctxBefore === null) S.ctxBefore = store.get("ctxBefore", s.context_before ?? 1);
    if (S.ctxAfter === null) S.ctxAfter = store.get("ctxAfter", s.context_after ?? 1);
    if (S.underline === null) S.underline = s.underline_entities ?? true;
    $("ctx-before").value = S.ctxBefore;
    $("ctx-after").value = S.ctxAfter;
    const w = store.get("rightWidth", s.panel_width || 440);
    document.documentElement.style.setProperty("--right-w", w + "px");
    $("layout").classList.toggle("left-closed", store.get("leftClosed", window.innerWidth < 640));
    $("layout").classList.toggle("right-closed", store.get("rightClosed", false));
    $("underline-toggle").classList.toggle("on", S.underline);
    $("panel-body").classList.toggle("no-underline", !S.underline);
    $("t-labels").classList.toggle("on", S.labels);
    for (const b of $("layout-toggle").querySelectorAll("button")) b.classList.toggle("on", b.dataset.layout === S.layout);
  }

  /* ============================================================ status */
  let polling = null;
  async function pollStatus() {
    clearTimeout(polling);
    let delay = 2000;
    try {
      const st = await api("/api/status");
      const bar = $("progress");
      if (st.state === "running") {
        bar.classList.remove("hidden");
        bar.classList.toggle("indeterminate", !st.progress);
        bar.querySelector(".bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
        bar.title = st.message;
        showBusy(st.message || "Working…");
        delay = 500;
      } else {
        bar.classList.add("hidden");
        if (st.state === "error") snack("Processing failed: " + st.error, null, 10000);
        if (st.version !== S.version && st.version > 0) {
          await refresh();
        }
      }
    } catch (e) {
      delay = 5000;
    }
    polling = setTimeout(pollStatus, delay);
  }
  let busyShown = "";
  function showBusy(msg) {
    if (busyShown === msg) return;
    busyShown = msg;
    snack(msg, null, 0);
  }

  async function refresh() {
    let g;
    try {
      const res = await fetch("/api/graph");
      g = await res.json();
    } catch (e) {
      snack("Could not load graph: " + e.message);
      return;
    }
    if (g.empty || !g.conversations || !g.conversations.length) {
      S.version = g.version ?? (g.status ? g.status.version : 0);
      $("empty").classList.remove("hidden");
      $("panel-body").innerHTML = `<div class="panel-empty"><span class="msi">forum</span>Transcripts appear here once uploaded.</div>`;
      $("tree").replaceChildren();
      $("legend").replaceChildren();
      $("conv-count").textContent = "0";
      if (network) { network.destroy(); network = null; }
      S.data = null;
      renderStats(null);
      if (busyShown) { $("snackbar").classList.remove("show"); busyShown = ""; }
      if (g.warnings && g.warnings.length) renderWarnings(g.warnings);
      return;
    }
    $("empty").classList.add("hidden");
    const first = S.version < 0 || !S.data;
    ingest(g);
    buildNetwork();
    renderTree();
    renderLegend();
    renderLayers();
    renderStats(g.stats);
    renderWarnings(g.warnings);
    if (!S.currentConv || !S.convs.has(S.currentConv)) S.currentConv = S.convOrder.find((c) => !S.hiddenConvs.has(c)) || S.convOrder[0];
    if (S.search) runSearch(S.search.q, false);
    if (S.selected && S.nodes.has(S.selected)) selectNode(S.selected, { quiet: true });
    else { S.selected = null; renderSelection(); renderPanel(); }
    if (busyShown) {
      busyShown = "";
      snack(`${plural(g.stats.conversations, "conversation")} · ${fmt(g.stats.nodes)} nodes · ${fmt(g.stats.edges)} edges`, null, 3500);
    } else if (first && g.stats.nodes > 4000) {
      snack("Large graph – raise “Minimum mentions” in Settings for a lighter view.", { label: "Settings", run: () => (location.href = "/settings#graph-content") }, 8000);
    }
  }

  /* ============================================================ ingest */
  function ingest(g) {
    S.data = g;
    S.version = g.version;
    S.kinds.clear();
    for (const k of [...g.kinds.node_types, ...g.kinds.categories]) {
      S.kinds.set(k.key, { ...k, color: S.settings["color." + k.key] || k.color });
    }
    S.paras.clear(); S.events.clear(); S.convs.clear(); S.convParas.clear(); S.paraPos.clear();
    S.nodes.clear(); S.paraEnts.clear(); S.edgesByNode.clear(); S.textCache.clear();
    S.convOrder = g.conversations.map((c) => c.id);
    for (const c of g.conversations) { S.convs.set(c.id, c); S.convParas.set(c.id, []); }
    for (const p of g.paragraphs) {
      S.paras.set(p.id, p);
      const list = S.convParas.get(p.c);
      if (list) { S.paraPos.set(p.id, list.length); list.push(p.id); }
    }
    for (const e of g.events) S.events.set(e.id, e);
    for (const n of g.nodes) {
      S.nodes.set(n.id, n);
      if (n.type === "entity") {
        for (const [pid, s, e] of n.occ) {
          if (!S.paraEnts.has(pid)) S.paraEnts.set(pid, []);
          S.paraEnts.get(pid).push([s, e, n.id]);
        }
      }
    }
    for (const e of g.edges) {
      for (const end of [e.from, e.to]) {
        if (!S.edgesByNode.has(end)) S.edgesByNode.set(end, []);
        S.edgesByNode.get(end).push(e);
      }
    }
  }

  const kindKey = (n) => (n.type === "entity" ? n.category : n.type);
  const kindOf = (n) => S.kinds.get(kindKey(n)) || { color: "#9aa0a6", icon: "circle", label: kindKey(n), group: "Other" };
  const convVisible = (c) => !S.hiddenConvs.has(c);

  /* =========================================================== filters */
  function computeVisible() {
    const vis = new Set();
    for (const n of S.nodes.values()) {
      if (n.conv && n.conv.length && !n.conv.some(convVisible)) continue;
      if (S.hiddenLayers.has(n.layer)) continue;
      if (S.hiddenKinds.has(kindKey(n))) continue;
      vis.add(n.id);
    }
    // entities left without any visible connection are hidden too
    for (const id of [...vis]) {
      const n = S.nodes.get(id);
      if (n.type !== "entity" && n.type !== "tool_hub") continue;
      const edges = S.edgesByNode.get(id) || [];
      if (!edges.some((e) => edgeOk(e, vis))) vis.delete(id);
    }
    S.visibleNodes = vis;
  }
  function edgeOk(e, vis = S.visibleNodes) {
    return vis.has(e.from) && vis.has(e.to) && (!e.conv || convVisible(e.conv));
  }
  function applyFilters() {
    computeVisible();
    if (nodesView) { nodesView.refresh(); edgesView.refresh(); }
    if (S.layout === "layers") applyLayout(false);
    renderStats(S.data && S.data.stats);
    store.set("hiddenConvs", [...S.hiddenConvs]);
    store.set("hiddenKinds", [...S.hiddenKinds]);
    store.set("hiddenLayers", [...S.hiddenLayers]);
  }

  /* =========================================================== network */
  const graphColors = () => ({
    ink: SS.cssVar("--on-surface"),
    inkVariant: SS.cssVar("--on-surface-variant"),
    surface: SS.cssVar("--surface"),
    outline: SS.cssVar("--outline"),
    outlineVariant: SS.cssVar("--outline-variant"),
    primary: SS.cssVar("--primary"),
    mark: SS.cssVar("--mark-strong"),
  });
  let GC = null;

  function entityRadius(n) {
    const s = S.settings;
    const min = s.node_min ?? 8, max = s.node_max ?? 38;
    const mode = s.size_by || "mentions";
    if (mode === "fixed") return min + 4;
    const v = mode === "degree" ? n.deg : n.count;
    const maxV = mode === "degree" ? S.maxDeg : S.maxCount;
    return min + (max - min) * Math.sqrt(Math.max(0, (v || 1) - 1) / Math.max(1, maxV - 1));
  }

  function truncate(label, n) {
    label = String(label).replace(/\s+/g, " ");
    return label.length > n ? label.slice(0, n - 1) + "…" : label;
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    if (ctx.roundRect) { ctx.roundRect(x, y, w, h, r); return; }
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function isDimmed(id) {
    if (S.neighbors && !S.neighbors.has(id)) return true;
    if (S.search && S.search.nodeIds.size && !S.search.nodeIds.has(id) && !S.neighbors) return true;
    return false;
  }

  function measure(ctx, text, font) {
    const key = font + "\u0000" + text;
    let w = S.textCache.get(key);
    if (w === undefined) { ctx.font = font; w = ctx.measureText(text).width; S.textCache.set(key, w); }
    return w;
  }

  function renderNode({ ctx, id, x, y, state: { selected, hover } }) {
    const n = S.nodes.get(id);
    const k = kindOf(n);
    const s = S.settings;
    const fs = s.font_size || 13;
    const dim = isDimmed(id);
    const matched = S.search && S.search.nodeIds.has(id);
    const thoughtAlpha = n.layer === "thought" ? (s.thought_opacity ?? 0.75) : 1;
    const alpha = (dim ? 0.15 : 1) * thoughtAlpha;
    const scale = network ? network.getScale() : 1;

    if (n.type === "entity" || n.type === "tool_hub") {
      const r = n.type === "tool_hub" ? 16 : entityRadius(n);
      const label = truncate(n.label, s.label_max || 32);
      return {
        drawNode() {
          ctx.save();
          ctx.globalAlpha = alpha;
          if (matched) { ctx.beginPath(); ctx.arc(x, y, r + 7, 0, 2 * Math.PI); ctx.fillStyle = GC.mark; ctx.globalAlpha = alpha * 0.55; ctx.fill(); ctx.globalAlpha = alpha; }
          if (selected || hover) { ctx.beginPath(); ctx.arc(x, y, r + 4, 0, 2 * Math.PI); ctx.lineWidth = 3; ctx.strokeStyle = GC.primary; ctx.stroke(); }
          ctx.beginPath();
          ctx.arc(x, y, r, 0, 2 * Math.PI);
          ctx.fillStyle = k.color;
          ctx.fill();
          ctx.lineWidth = 2;
          ctx.strokeStyle = GC.surface;
          if (n.layer === "thought") ctx.setLineDash([3, 3]);
          ctx.stroke();
          ctx.setLineDash([]);
          if (r >= 9 && scale * r > 5) {
            ctx.font = `${Math.round(r * 1.15)}px "Material Symbols Outlined"`;
            ctx.fillStyle = "#ffffff";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(k.icon, x, y + 0.5);
          }
          ctx.restore();
        },
        drawExternalLabel() {
          const show = selected || hover || matched || (S.labels && scale * fs >= 7) || (S.labels && r > 20 && scale * fs >= 4);
          if (!show || dim) return;
          ctx.save();
          ctx.font = `${fs}px Roboto, sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "top";
          ctx.lineJoin = "round";
          ctx.lineWidth = 4;
          ctx.strokeStyle = GC.surface;
          ctx.globalAlpha = thoughtAlpha;
          ctx.strokeText(label, x, y + r + 4);
          ctx.fillStyle = GC.ink;
          ctx.fillText(label, x, y + r + 4);
          ctx.restore();
        },
        nodeDimensions: { width: 2 * r, height: 2 * r },
      };
    }

    // structural node: Material "chip"
    const small = n.type === "tool_arg";
    const f = small ? `${fs - 1}px "Roboto Mono", monospace` : `500 ${fs}px Roboto, sans-serif`;
    const text = truncate(n.label, small ? (s.label_max || 32) + 12 : s.label_max || 32);
    const iconSize = fs + 5;
    const h = fs + (small ? 10 : 16);
    const w = measure(ctx, text, f) + iconSize + (small ? 18 : 26) + (n.type === "conversation" ? 8 : 0);
    const left = x - w / 2, top = y - h / 2;
    return {
      drawNode() {
        ctx.save();
        ctx.globalAlpha = alpha;
        if (matched) { roundRect(ctx, left - 6, top - 6, w + 12, h + 12, h / 2 + 6); ctx.fillStyle = GC.mark; ctx.globalAlpha = alpha * 0.55; ctx.fill(); ctx.globalAlpha = alpha; }
        if (selected || hover) { roundRect(ctx, left - 3.5, top - 3.5, w + 7, h + 7, h / 2 + 3.5); ctx.lineWidth = 3; ctx.strokeStyle = GC.primary; ctx.stroke(); }
        let color = k.color;
        if (n.type === "conversation") { const c = S.convs.get(n.conv[0]); if (c) color = c.color; }
        roundRect(ctx, left, top, w, h, n.type === "conversation" || small ? h / 2 : 8);
        if (small) {
          ctx.fillStyle = GC.surface; ctx.fill();
          ctx.lineWidth = 1.5; ctx.strokeStyle = color; ctx.stroke();
        } else {
          ctx.fillStyle = color; ctx.fill();
          if (n.type === "thought") { ctx.setLineDash([4, 3]); ctx.lineWidth = 2; ctx.strokeStyle = GC.surface; ctx.stroke(); ctx.setLineDash([]); }
          if (n.error) { ctx.lineWidth = 3; ctx.strokeStyle = "#d93025"; ctx.stroke(); }
        }
        const ink = small ? GC.ink : "#ffffff";
        ctx.fillStyle = small ? color : ink;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.font = `${iconSize}px "Material Symbols Outlined"`;
        ctx.fillText(k.icon, left + (small ? 7 : 10), y + 0.5);
        ctx.font = f;
        ctx.fillStyle = ink;
        ctx.fillText(text, left + iconSize + (small ? 10 : 14), y + 0.5);
        ctx.restore();
      },
      nodeDimensions: { width: w, height: h },
    };
  }

  function edgeStyle(e) {
    const s = S.settings;
    const conv = e.conv ? S.convs.get(e.conv) : null;
    const labels = s.edge_labels || "relations";
    const base = { id: e.id, from: e.from, to: e.to, arrows: s.arrows === false ? "" : { to: { enabled: true, scaleFactor: 0.45 } } };
    const col = (c, o = 1) => ({ color: c, highlight: c, hover: c, opacity: o });
    const purple = S.kinds.get("thought")?.color || "#9334e6";
    let style;
    switch (e.type) {
      case "flow":
        style = { width: 2.2, color: col(s.color_flow_by_conversation !== false && conv ? conv.color : GC.inkVariant, 0.85) };
        break;
      case "thinks":
      case "leads_to":
        style = { width: 1.5, dashes: [5, 4], color: col(purple, 0.7) };
        break;
      case "returns":
        style = { width: 1.5, color: col(S.kinds.get("tool_result")?.color || "#129eaf", 0.8) };
        break;
      case "arg":
        style = { width: 1, color: col(S.kinds.get("tool_arg")?.color || "#b06000", 0.7) };
        break;
      case "uses":
        style = { width: 1, dashes: [2, 3], color: col(S.kinds.get("tool_hub")?.color || "#e52592", 0.6) };
        break;
      case "relation":
        style = { width: 1.2 + Math.min(3, Math.log2(e.w || 1)), color: col(GC.inkVariant, 0.9), label: labels !== "none" ? e.label : undefined,
          font: { size: 11, color: GC.inkVariant, strokeWidth: 3, strokeColor: GC.surface, align: "middle" } };
        break;
      case "alias":
        style = { width: 1, dashes: [2, 4], color: col(S.kinds.get("file_path")?.color || "#5f6368", 0.8), label: labels === "all" ? e.label : undefined };
        break;
      case "cooccurs":
        style = { width: Math.min(4, 0.5 + Math.log2(e.w || 1)), dashes: [1, 4], color: col(GC.outline, 0.5), arrows: "" };
        break;
      default: // mention
        style = { width: 0.6 + Math.min(2.5, Math.log2(e.w || 1) * 0.6), color: col(GC.outline, 0.45),
          label: labels === "all" && e.label ? e.label : undefined, font: { size: 10, color: GC.inkVariant, strokeWidth: 3, strokeColor: GC.surface } };
        if (e.thought) style.dashes = [3, 3];
    }
    if (S.neighbors && !(S.neighbors.has(e.from) && S.neighbors.has(e.to) && (e.from === S.selected || e.to === S.selected))) {
      style.color = { ...style.color, opacity: 0.08 };
      style.label = undefined;
    }
    return { ...base, ...style };
  }

  function physicsOptions(enabled = true) {
    const s = S.settings;
    const g = s.gravity ?? -60;
    return {
      enabled,
      solver: S.layout === "layers" ? "repulsion" : s.solver || "forceAtlas2Based",
      forceAtlas2Based: { gravitationalConstant: g, centralGravity: s.central_gravity ?? 0.01, springLength: s.spring_length ?? 120,
        springConstant: s.spring_constant ?? 0.08, damping: s.damping ?? 0.4, avoidOverlap: s.avoid_overlap ?? 0.2 },
      barnesHut: { gravitationalConstant: g * 40, centralGravity: (s.central_gravity ?? 0.01) * 10, springLength: s.spring_length ?? 120,
        springConstant: (s.spring_constant ?? 0.08) / 2, damping: s.damping ?? 0.4, avoidOverlap: s.avoid_overlap ?? 0.2 },
      repulsion: { nodeDistance: (s.spring_length ?? 120) * 0.9, centralGravity: 0, springLength: (s.spring_length ?? 120) * 0.8,
        springConstant: 0.03, damping: 0.5 },
      stabilization: { enabled: true, iterations: s.stabilization ?? 250, updateInterval: 25, fit: true },
      minVelocity: 1,
      maxVelocity: 60,
    };
  }

  function buildNetwork() {
    GC = graphColors();
    let maxCount = 1, maxDeg = 1;
    for (const n of S.nodes.values()) if (n.type === "entity") { maxCount = Math.max(maxCount, n.count || 1); maxDeg = Math.max(maxDeg, n.deg || 1); }
    S.maxCount = maxCount; S.maxDeg = maxDeg;

    const prevPositions = network ? network.getPositions() : {};
    const nodes = [];
    for (const n of S.nodes.values()) {
      const item = { id: n.id, shape: "custom", ctxRenderer: renderNode, label: n.label };
      if (prevPositions[n.id]) { item.x = prevPositions[n.id].x; item.y = prevPositions[n.id].y; }
      nodes.push(item);
    }
    computeVisible();
    S.edgeById = new Map(S.data.edges.map((e) => [e.id, e]));
    nodesDS = new vis.DataSet(nodes);
    edgesDS = new vis.DataSet(S.data.edges.map(edgeStyle));
    nodesView = new vis.DataView(nodesDS, { filter: (item) => S.visibleNodes.has(item.id) });
    edgesView = new vis.DataView(edgesDS, { filter: (item) => {
      const e = S.edgeById.get(item.id);
      return e ? edgeOk(e) : false;
    } });

    const options = {
      autoResize: true,
      layout: { improvedLayout: S.nodes.size < 400, randomSeed: 7 },
      physics: physicsOptions(true),
      interaction: { hover: true, tooltipDelay: 3600000, hideEdgesOnDrag: S.nodes.size > 1500, hideEdgesOnZoom: S.nodes.size > 3000,
        multiselect: false, navigationButtons: false, keyboard: false, zoomSpeed: 0.8 },
      edges: { smooth: smoothOption(), selectionWidth: 1.5, hoverWidth: 0.5 },
      nodes: { chosen: false },
    };
    if (network) network.destroy();
    network = new vis.Network($("graph"), { nodes: nodesView, edges: edgesView }, options);
    S.physics = true;
    updatePhysicsButton();
    network.on("stabilizationProgress", (p) => {
      const bar = $("progress");
      bar.classList.remove("hidden", "indeterminate");
      bar.querySelector(".bar").style.width = `${Math.round((100 * p.iterations) / p.total)}%`;
    });
    network.once("stabilizationIterationsDone", () => {
      $("progress").classList.add("hidden");
      if (!S.settings.keep_physics) setPhysics(false);
    });
    network.on("click", onClick);
    network.on("doubleClick", (p) => { if (p.nodes.length) network.focus(p.nodes[0], { scale: Math.max(1.2, network.getScale()), animation: { duration: 500 } }); });
    network.on("hoverNode", (p) => showNodeTip(p.node));
    network.on("blurNode", hideTip);
    network.on("hoverEdge", (p) => showEdgeTip(p.edge));
    network.on("blurEdge", hideTip);
    network.on("dragStart", hideTip);
    network.on("zoom", hideTip);
    if (S.layout === "layers") applyLayout(true);
  }

  function smoothOption() {
    const t = S.settings.edge_smooth || "continuous";
    if (t === "straight") return false;
    return { enabled: true, type: t, roundness: 0.35 };
  }

  function restyleEdges() {
    if (!edgesDS) return;
    edgesDS.update(S.data.edges.map(edgeStyle));
  }

  function setPhysics(on) {
    S.physics = on;
    if (network) network.setOptions({ physics: on ? { ...physicsOptions(true), stabilization: false } : { enabled: false } });
    updatePhysicsButton();
  }
  function updatePhysicsButton() {
    const b = $("t-physics");
    b.classList.toggle("on", S.physics);
    b.querySelector(".msi").textContent = S.physics ? "pause" : "play_arrow";
    b.title = S.physics ? "Freeze layout" : "Resume physics";
  }

  /* ------------------------------------------------------------ layouts */
  function hashJitter(id) {
    let h = 2166136261;
    for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 1000) / 1000 - 0.5;
  }

  function applyLayout(stabilize = true) {
    if (!network) return;
    const s = S.settings;
    const updates = [];
    if (S.layout !== "layers") {
      for (const id of S.nodes.keys()) updates.push({ id, fixed: false });
      nodesDS.update(updates);
      network.setOptions({ physics: physicsOptions(true) });
      S.physics = true;
      updatePhysicsButton();
      if (stabilize) network.stabilize(s.stabilization ?? 250);
      network.once("stabilized", () => { if (!s.keep_physics) setPhysics(false); });
      return;
    }
    // Swim-lane timeline: every conversation starts a new row and long ones
    // wrap, so the drawing keeps roughly the canvas' aspect ratio.  Inside a
    // row, bands separate thoughts / dialogue / actions / entities.
    const G = s.layer_gap || 200, step = s.step_gap || 170;
    const band = { thoughtEntity: -1.55 * G, thought: -0.8 * G, dialogue: 0, action: 0.7 * G, arg: 1.15 * G, entity: 2.0 * G };
    const pitch = 4.7 * G;
    const seqs = [];
    let total = 0;
    for (const cid of S.convOrder) {
      if (!convVisible(cid)) continue;
      const items = [];
      if (S.visibleNodes.has(`conv:${cid}`)) items.push(`conv:${cid}`);
      for (const ev of S.data.events) if (ev.c === cid && S.visibleNodes.has(ev.id)) items.push(ev.id);
      if (items.length) { seqs.push(items); total += items.length; }
    }
    const rect = $("graph").getBoundingClientRect();
    const aspect = Math.max(0.5, rect.width / Math.max(1, rect.height));
    const rowsFor = (W) => seqs.reduce((a, items) => a + Math.ceil(items.length / W), 0);
    let W = Math.max(12, Math.round(Math.sqrt((aspect * total * pitch) / step)));
    for (let i = 0; i < 40 && (W * step) / (rowsFor(W) * pitch) < aspect * 0.8; i++) W = Math.ceil(W * 1.15);
    const pos = new Map();
    let row = 0;
    for (const items of seqs) {
      items.forEach((id, i) => {
        if (i > 0 && i % W === 0) row++;
        const n = S.nodes.get(id);
        const x = (i % W) * step, base = row * pitch;
        pos.set(id, { x, y: base + (band[n.layer] ?? 0), row });
        if (n.type === "tool_call") {
          const args = (S.edgesByNode.get(id) || []).filter((e) => e.type === "arg" && S.visibleNodes.has(e.to));
          args.forEach((e, j) => pos.set(e.to, { x: x + (j - (args.length - 1) / 2) * step * 0.55, y: base + band.arg + (j % 2) * 0.18 * G, row }));
        }
      });
      row++;
    }
    for (const id of S.visibleNodes) {
      const n = S.nodes.get(id);
      if (pos.has(id)) {
        const p = pos.get(id);
        updates.push({ id, x: p.x, y: p.y, fixed: { x: true, y: true } });
      } else if (n.type === "entity" || n.type === "tool_hub") {
        const near = (S.edgesByNode.get(id) || []).map((e) => pos.get(e.from === id ? e.to : e.from)).filter(Boolean);
        const rows = new Map();
        for (const p of near) rows.set(p.row, (rows.get(p.row) || 0) + 1);
        const r = near.length ? [...rows.entries()].sort((a, b) => b[1] - a[1])[0][0] : 0;
        const xs = near.filter((p) => p.row === r).map((p) => p.x);
        const x = xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
        const y = r * pitch + (n.layer === "thought" ? band.thoughtEntity + hashJitter(id) * 0.4 * G : band.entity + hashJitter(id) * 0.7 * G);
        updates.push({ id, x: x + hashJitter(id + "x") * step, y, fixed: { x: false, y: true } });
      }
    }
    nodesDS.update(updates);
    network.setOptions({ physics: physicsOptions(true) });
    S.physics = true;
    updatePhysicsButton();
    if (stabilize) {
      network.stabilize(Math.min(s.stabilization ?? 250, 200));
      network.once("stabilized", () => { if (!s.keep_physics) setPhysics(false); network.fit({ animation: { duration: 400 } }); });
    }
  }

  function setLayout(layout) {
    S.layout = layout;
    for (const b of $("layout-toggle").querySelectorAll("button")) b.classList.toggle("on", b.dataset.layout === layout);
    api("/api/settings", { method: "PUT", body: { layout } }).catch(() => {});
    S.settings.layout = layout;
    applyLayout(true);
  }

  /* =========================================================== tooltip */
  function tipHead(n) {
    const k = kindOf(n);
    const convs = (n.conv || []).length;
    let sub = k.label;
    if (n.type === "entity") sub += ` · ${plural(n.count, "mention")} · ${plural(convs, "conversation")}`;
    else if (n.conv && n.conv[0] && S.convs.get(n.conv[0])) sub += ` · ${S.convs.get(n.conv[0]).title}`;
    return el("div", { class: "tt-head" },
      el("span", { class: "ico", style: { background: k.color } }, icon(k.icon, "sm")),
      el("div", { class: "grow" }, el("div", { class: "tt-title" }, n.label), el("div", { class: "tt-sub" }, sub)));
  }

  function snippet(pid, s, e, radius = 260) {
    const p = S.paras.get(pid);
    if (!p) return el("div");
    const t = p.t;
    const whole = e - s >= t.length - 1;
    let a = whole ? 0 : Math.max(0, s - radius), b = whole ? Math.min(t.length, radius * 2) : Math.min(t.length, e + radius);
    const box = el("div", { class: "tt-para" + (p.code || p.r.startsWith("tool") ? " mono" : "") });
    if (a > 0) box.append("…");
    box.append(t.slice(a, whole ? b : s));
    if (!whole) { box.append(el("mark", { class: "sel" }, t.slice(s, e))); box.append(t.slice(e, b)); }
    if (b < t.length) box.append("…");
    return box;
  }

  function showNodeTip(id) {
    if (S.settings.hover_tooltips === false) return;
    const n = S.nodes.get(id);
    if (!n) return;
    const tip = $("tooltip");
    tip.replaceChildren(tipHead(n));
    const occ = visibleOcc(n);
    if (occ.length) {
      tip.append(snippet(occ[0][0], occ[0][1], occ[0][2]));
      const pids = new Set(occ.map((o) => o[0]));
      const more = pids.size - 1;
      tip.append(el("div", { class: "tt-foot" }, more > 0 ? `+ ${plural(more, "more paragraph")} · click to list them all` : "Click to open in the transcript"));
    }
    placeTip();
  }

  function showEdgeTip(eid) {
    const e = S.edgeById.get(eid);
    if (!e) return;
    const a = S.nodes.get(e.from), b = S.nodes.get(e.to);
    const verb = e.label ? `— ${e.label} →` : `— ${e.type.replace("_", " ")} →`;
    const tip = $("tooltip");
    tip.replaceChildren(el("div", { class: "tt-sub" }, `${a ? a.label : e.from}  ${verb}  ${b ? b.label : e.to}`),
      el("div", { class: "tt-foot" }, `${e.type}${e.w ? ` · ${e.w}×` : ""}`));
    placeTip();
  }

  function placeTip() {
    const tip = $("tooltip");
    const stage = $("stage").getBoundingClientRect();
    tip.classList.add("show");
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    let x = S.pointer.x - stage.left + 16, y = S.pointer.y - stage.top + 16;
    if (x + tw > stage.width - 8) x = Math.max(8, S.pointer.x - stage.left - tw - 16);
    if (y + th > stage.height - 8) y = Math.max(8, S.pointer.y - stage.top - th - 16);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hideTip() { $("tooltip").classList.remove("show"); }

  /* ========================================================= selection */
  function visibleOcc(n) {
    return (n.occ || []).filter(([pid]) => { const p = S.paras.get(pid); return p && convVisible(p.c); });
  }

  function onClick(p) {
    hideTip();
    if (p.nodes.length) selectNode(p.nodes[0]);
    else if (!p.edges.length) clearSelection();
  }

  function selectNode(id, { focus = false, quiet = false } = {}) {
    const n = S.nodes.get(id);
    if (!n) return;
    S.selected = id;
    S.neighbors = new Set([id, ...(S.edgesByNode.get(id) || []).map((e) => (e.from === id ? e.to : e.from))]);
    if (network && S.visibleNodes.has(id)) {
      network.selectNodes([id]);
      if (focus) network.focus(id, { scale: Math.max(network.getScale(), 0.9), animation: { duration: 450 } });
    }
    restyleEdges();
    network && network.redraw();
    renderSelection();
    const occ = visibleOcc(n);
    const eventsHit = new Set(occ.map(([pid]) => S.paras.get(pid).e));
    S.matchSource = { kind: "node", id, title: n.label, occ };
    if (quiet) { renderPanel(); return; }
    if (eventsHit.size <= 1 && occ.length) {
      const pid = occ[0][0];
      S.currentConv = S.paras.get(pid).c;
      setTab("transcript", { scrollTo: pid });
    } else {
      setTab(occ.length ? "matches" : "transcript");
    }
  }

  function clearSelection() {
    S.selected = null;
    S.neighbors = null;
    if (S.search) S.matchSource = searchSource();
    else S.matchSource = null;
    network && network.unselectAll();
    restyleEdges();
    network && network.redraw();
    renderSelection();
    renderPanel();
  }

  function renderSelection() {
    const box = $("selection");
    const n = S.selected && S.nodes.get(S.selected);
    if (!n) { box.classList.add("hidden"); box.replaceChildren(); return; }
    const k = kindOf(n);
    const convs = (n.conv || []).map((c) => S.convs.get(c)).filter(Boolean);
    const sub = el("div", { class: "sub" }, el("span", { class: "tag" }, k.label));
    if (n.type === "entity") sub.append(el("span", { class: "tag" }, plural(n.count, "mention")));
    const pidCount = new Set(visibleOcc(n).map((o) => o[0])).size;
    sub.append(el("span", { class: "tag" }, plural(pidCount, "paragraph")));
    sub.append(el("span", { class: "tag" }, plural(convs.length, "conversation")));
    sub.append(el("span", { class: "tag" }, plural(S.neighbors.size - 1, "link")));
    const body = el("div", { class: "grow" }, el("div", { class: "title" }, n.label), sub);
    if (n.type === "tool_call") {
      const ev = S.events.get(n.event);
      if (ev) body.append(el("div", { class: "args" }, ev.p.map((pid) => S.paras.get(pid).t).join("\n")));
    }
    box.replaceChildren(
      el("span", { class: "ico", style: { background: n.type === "conversation" && convs[0] ? convs[0].color : k.color } }, icon(k.icon)),
      body,
      el("div", {},
        el("button", { class: "icon-btn sm", title: "Centre in graph", onclick: () => network && network.focus(n.id, { scale: Math.max(1, network.getScale()), animation: { duration: 450 } }) }, icon("center_focus_strong", "sm")),
        el("button", { class: "icon-btn sm", title: "Clear selection", onclick: clearSelection }, icon("close", "sm"))));
    box.classList.remove("hidden");
  }

  /* ============================================================= panel */
  function setTab(tab, opts = {}) {
    S.tab = tab;
    for (const b of document.querySelectorAll(".tab")) b.classList.toggle("on", b.dataset.tab === tab);
    $("ctx-controls").classList.toggle("hidden", tab !== "matches");
    renderPanel(opts);
  }

  function renderPanel(opts = {}) {
    const count = S.matchSource ? new Set(S.matchSource.occ.map((o) => o[0])).size : 0;
    const badge = $("match-count");
    badge.textContent = fmt(count);
    badge.classList.toggle("hidden", !S.matchSource);
    if (S.tab === "matches") renderMatches();
    else renderTranscript(opts.scrollTo);
  }

  // marks for a paragraph: selection occurrences + search hits
  function marksFor(pid) {
    const out = [];
    if (S.matchSource && S.matchSource.kind === "node") {
      for (const o of S.matchSource.occ) if (o[0] === pid) out.push([o[1], o[2], "sel"]);
    }
    if (S.search) for (const o of S.search.byPara.get(pid) || []) out.push([o[0], o[1], ""]);
    return out;
  }

  function paraHTML(p, extraClass = "") {
    const t = p.t;
    const ranges = [];
    if (p.arg !== undefined && p.r === "tool_call") ranges.push([0, Math.min(t.length, p.arg.length + 1), "argkey"]);
    for (const [s, e, node] of S.paraEnts.get(p.id) || []) if (S.visibleNodes.has(node)) ranges.push([s, e, "ent", node]);
    for (const [s, e, cls] of marksFor(p.id)) ranges.push([s, e, "mark", cls]);
    const cuts = new Set([0, t.length]);
    for (const r of ranges) { cuts.add(Math.max(0, Math.min(t.length, r[0]))); cuts.add(Math.max(0, Math.min(t.length, r[1]))); }
    const pts = [...cuts].sort((a, b) => a - b);
    let html = "";
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      if (a === b) continue;
      let seg = esc(t.slice(a, b));
      const active = ranges.filter((r) => r[0] <= a && r[1] >= b);
      const mark = active.find((r) => r[2] === "mark");
      if (mark) seg = `<mark class="${mark[3]}">${seg}</mark>`;
      const ent = active.find((r) => r[2] === "ent");
      if (ent) {
        const n = S.nodes.get(ent[3]);
        seg = `<span class="ent" data-node="${esc(ent[3])}" style="--ent-c:${kindOf(n).color}" title="${esc(kindOf(n).label)}: ${esc(n.label)}">${seg}</span>`;
      }
      if (active.some((r) => r[2] === "argkey")) seg = `<span class="argkey">${seg}</span>`;
      html += seg;
    }
    const cls = ["para", extraClass];
    if (p.code) cls.push("code");
    return `<div class="${cls.join(" ")}" id="p-${esc(p.id)}" data-pid="${esc(p.id)}">${html}</div>`;
  }

  const ROLE_ICON = { user: "person", assistant: "smart_toy", system: "settings", thought: "psychology", tool_call: "build", tool_result: "output" };
  function roleColor(type) { return (S.kinds.get(type) || {}).color || "#80868b"; }
  function avatar(type) {
    return `<span class="avatar" style="background:${roleColor(type)}"><span class="msi">${ROLE_ICON[type] || "chat"}</span></span>`;
  }
  function fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    return isNaN(d) ? esc(ts) : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function convPicker() {
    const sel = el("select", { class: "select", "aria-label": "Conversation" });
    const groups = new Map();
    for (const cid of S.convOrder) {
      const c = S.convs.get(cid);
      const g = `${c.host} / ${c.user} / ${c.harness}`;
      if (!groups.has(g)) groups.set(g, el("optgroup", { label: g }));
      groups.get(g).append(el("option", { value: cid, selected: cid === S.currentConv }, (convVisible(cid) ? "" : "(hidden) ") + c.title));
    }
    sel.append(...groups.values());
    sel.addEventListener("change", () => { S.currentConv = sel.value; renderTranscript(); });
    return sel;
  }

  function renderTranscript(scrollTo) {
    const body = $("panel-body");
    const cid = S.currentConv;
    const c = S.convs.get(cid);
    if (!c) { body.innerHTML = `<div class="panel-empty"><span class="msi">forum</span>No conversation selected</div>`; return; }
    const head = el("div", { class: "conv-picker" }, el("span", { class: "dot", style: { width: "12px", height: "12px", borderRadius: "50%", background: c.color, flex: "none" } }), convPicker(),
      el("button", { class: "icon-btn sm", title: "Show this conversation in the graph", onclick: () => fitConversation(cid) }, icon("center_focus_strong", "sm")));
    const meta = el("div", { class: "conv-meta" },
      el("span", { class: "tag" }, icon("computer", "xs"), c.host), el("span", { class: "tag" }, icon("person", "xs"), c.user),
      el("span", { class: "tag" }, icon("terminal", "xs"), c.harness), c.model ? el("span", { class: "tag" }, icon("smart_toy", "xs"), c.model) : null,
      el("span", { class: "tag", title: c.source }, icon("description", "xs"), c.session));
    const collapse = S.settings.collapse_tool_results !== false;
    let html = "";
    for (const ev of S.data.events) {
      if (ev.c !== cid) continue;
      const long = ev.type === "tool_result" && collapse && ev.p.reduce((a, pid) => a + S.paras.get(pid).t.length, 0) > 700;
      const hasMark = scrollTo && ev.p.includes(scrollTo);
      const cls = `msg ${ev.type}${ev.error ? " error" : ""}${long && !hasMark ? " collapsed" : ""}`;
      html += `<div class="${cls}" data-event="${esc(ev.id)}"><div class="msg-head">${avatar(ev.type)}<span class="who">${esc(ev.label)}</span>` +
        (ev.call_id ? `<span class="tag mono">${esc(ev.call_id)}</span>` : "") +
        `<span class="ts">${fmtTime(ev.ts)}</span><button class="icon-btn sm jump" data-jump="${esc(ev.id)}" title="Show in graph"><span class="msi xs">my_location</span></button></div>` +
        `<div class="msg-body">${ev.p.map((pid) => paraHTML(S.paras.get(pid))).join("")}</div>` +
        (long && !hasMark ? `<button class="btn text sm expand-btn" data-expand="1"><span class="msi">expand_more</span>Show full result</button>` : "") + `</div>`;
    }
    const list = el("div", { html });
    body.replaceChildren(head, meta, list);
    if (scrollTo) {
      const target = document.getElementById("p-" + scrollTo);
      if (target) {
        requestAnimationFrame(() => {
          body.scrollTop = target.offsetTop - body.clientHeight / 3;
          target.classList.add("flash");
          const m = target.querySelector("mark.sel");
          if (m) m.scrollIntoView({ block: "center" });
          setTimeout(() => target.classList.remove("flash"), 2200);
        });
      }
    } else {
      body.scrollTop = 0;
    }
  }

  function searchSource() {
    return S.search ? { kind: "search", title: `“${S.search.q}”`, occ: S.search.occ } : null;
  }

  function renderMatches() {
    const body = $("panel-body");
    const src = S.matchSource;
    if (!src || !src.occ.length) {
      body.innerHTML = `<div class="panel-empty"><span class="msi">travel_explore</span>${src ? "No matches in the visible conversations." : "Click a node or search to list the paragraphs it appears in."}</div>`;
      return;
    }
    const before = S.ctxBefore, after = S.ctxAfter;
    const hitsByConv = new Map();
    for (const o of src.occ) {
      const p = S.paras.get(o[0]);
      if (!p || !convVisible(p.c)) continue;
      if (!hitsByConv.has(p.c)) hitsByConv.set(p.c, new Set());
      hitsByConv.get(p.c).add(S.paraPos.get(o[0]));
    }
    const limit = S.settings.max_matches || 200;
    let shown = 0, total = 0;
    const frag = document.createDocumentFragment();
    frag.append(el("div", { class: "more-note" }, `${plural(new Set(src.occ.map((o) => o[0])).size, "paragraph")} matching ${src.title} in ${plural(hitsByConv.size, "conversation")}`));
    for (const cid of S.convOrder) {
      const hits = hitsByConv.get(cid);
      if (!hits) continue;
      const c = S.convs.get(cid);
      const list = S.convParas.get(cid);
      const idx = [...hits].sort((a, b) => a - b);
      total += idx.length;
      // merge overlapping context windows
      const windows = [];
      for (const i of idx) {
        const a = Math.max(0, i - before), b = Math.min(list.length - 1, i + after);
        const last = windows[windows.length - 1];
        if (last && a <= last.b + 1) { last.b = Math.max(last.b, b); last.hits.push(i); } else windows.push({ a, b, hits: [i] });
      }
      for (const w of windows) {
        if (shown >= limit) break;
        shown += w.hits.length;
        const firstHit = S.paras.get(list[w.hits[0]]);
        const ev = S.events.get(firstHit.e);
        let html = "";
        let lastEvent = null;
        for (let i = w.a; i <= w.b; i++) {
          const p = S.paras.get(list[i]);
          if (p.e !== lastEvent) {
            const e = S.events.get(p.e);
            html += `<div class="role">${avatar(e.type)}${esc(e.label)}<span class="muted" style="margin-left:auto">${fmtTime(e.ts)}</span></div>`;
            lastEvent = p.e;
          }
          const isHit = w.hits.includes(i);
          const mono = p.code || p.r === "tool_call" || p.r === "tool_result";
          html += paraHTML(p, (isHit ? "hit" : "ctx") + (mono ? " mono" : ""));
        }
        const group = el("div", { class: "match-group" },
          el("div", { class: "match-head" }, el("span", { class: "dot", style: { background: c.color } }),
            el("span", { class: "t grow" }, c.title), el("span", {}, ev ? ev.label : ""),
            el("button", { class: "icon-btn sm", title: "Open in transcript", "data-open": list[w.hits[0]] }, icon("open_in_new", "sm"))),
          el("div", { class: "match-body", html }));
        frag.append(group);
      }
    }
    if (shown < total) frag.append(el("div", { class: "more-note" }, `Showing ${fmt(shown)} of ${fmt(total)} matches – raise “Maximum matches listed” in Settings.`));
    body.replaceChildren(frag);
    body.scrollTop = 0;
  }

  function onPanelClick(ev) {
    const t = ev.target;
    const ent = t.closest(".ent");
    if (ent) {
      selectNode(ent.dataset.node, { focus: true, quiet: false });
      return;
    }
    const open = t.closest("[data-open]");
    if (open) {
      const pid = open.dataset.open;
      S.currentConv = S.paras.get(pid).c;
      setTab("transcript", { scrollTo: pid });
      return;
    }
    const jump = t.closest("[data-jump]");
    if (jump) {
      const id = jump.dataset.jump;
      if (S.visibleNodes.has(id)) { selectNode(id, { quiet: true }); network.focus(id, { scale: Math.max(1, network.getScale()), animation: { duration: 450 } }); }
      else snack("That node is hidden by the current filters.");
      return;
    }
    const exp = t.closest("[data-expand]");
    if (exp) {
      const msg = exp.closest(".msg");
      msg.classList.remove("collapsed");
      exp.remove();
    }
  }

  function fitConversation(cid) {
    if (!network) return;
    const ids = [...S.visibleNodes].filter((id) => { const n = S.nodes.get(id); return n.type !== "entity" && n.conv && n.conv.includes(cid); });
    if (ids.length) network.fit({ nodes: ids, animation: { duration: 500 } });
  }

  /* ============================================================ search */
  function makeRegex(q) {
    const m = q.match(/^\/(.+)\/([a-z]*)$/);
    try {
      if (m) return new RegExp(m[1], m[2].includes("g") ? m[2] : m[2] + "g");
    } catch (e) {
      return null;
    }
    return new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  }

  function runSearch(q, switchTab = true) {
    q = q.trim();
    $("q-clear").classList.toggle("hidden", !q);
    if (!q) {
      S.search = null;
      $("q-result").textContent = "";
      if (!S.selected) S.matchSource = null;
      network && network.redraw();
      renderPanel();
      return;
    }
    const re = makeRegex(q);
    if (!re) { $("q-result").textContent = "invalid regex"; return; }
    const occ = [];
    const byPara = new Map();
    for (const p of S.paras.values()) {
      if (!convVisible(p.c)) continue;
      re.lastIndex = 0;
      let m, guard = 0;
      while ((m = re.exec(p.t)) && guard++ < 200) {
        if (!m[0].length) { re.lastIndex++; continue; }
        occ.push([p.id, m.index, m.index + m[0].length]);
        if (!byPara.has(p.id)) byPara.set(p.id, []);
        byPara.get(p.id).push([m.index, m.index + m[0].length]);
      }
      if (occ.length > 20000) break;
    }
    const nodeIds = new Set();
    for (const id of S.visibleNodes) {
      re.lastIndex = 0;
      if (re.test(S.nodes.get(id).label)) nodeIds.add(id);
    }
    S.search = { q, re, occ, byPara, nodeIds };
    $("q-result").textContent = `${plural(nodeIds.size, "node")} · ${plural(byPara.size, "paragraph")}`;
    if (!S.selected || switchTab) { S.selected = null; S.neighbors = null; renderSelection(); restyleEdges(); S.matchSource = searchSource(); }
    network && network.redraw();
    if (switchTab) setTab("matches"); else renderPanel();
  }

  /* ============================================================== tree */
  function buildTree() {
    const root = { key: "root", children: new Map(), convs: [] };
    for (const cid of S.convOrder) {
      const c = S.convs.get(cid);
      const path = [["host", c.host, "computer"], ["user", c.user, "person"], ["harness", c.harness, "terminal"]];
      let node = root;
      let key = "";
      for (const [kind, name, ic] of path) {
        key += "/" + name;
        if (!node.children.has(name)) node.children.set(name, { key: kind + ":" + key, kind, name, icon: ic, children: new Map(), convs: [] });
        node = node.children.get(name);
      }
      node.convs.push(cid);
    }
    return root;
  }
  function descendantConvs(node) {
    const out = [...node.convs];
    for (const ch of node.children.values()) out.push(...descendantConvs(ch));
    return out;
  }
  function cbIcon(convs) {
    const vis = convs.filter(convVisible).length;
    return vis === 0 ? "check_box_outline_blank" : vis === convs.length ? "check_box" : "indeterminate_check_box";
  }

  function renderTree() {
    const tree = $("tree");
    const root = buildTree();
    const counts = S.convSearch ? S.convSearch.counts : null;
    $("conv-count").textContent = fmt(S.convOrder.length);
    const frag = document.createDocumentFragment();
    const renderNode = (node, container) => {
      const convs = descendantConvs(node);
      if (counts && !convs.some((c) => counts.get(c))) return;
      const collapsed = S.collapsed.has(node.key) && !counts;
      const cb = cbIcon(convs);
      const hitTotal = counts ? convs.reduce((a, c) => a + (counts.get(c) || 0), 0) : 0;
      const row = el("div", { class: "tree-row", "data-key": node.key },
        el("span", { class: "twisty", "data-twisty": node.key }, icon(collapsed ? "chevron_right" : "expand_more", "sm")),
        el("span", { class: `cb${cb !== "check_box_outline_blank" ? " on" : ""}`, "data-toggle": node.key }, icon(cb, "sm")),
        icon(node.icon, "sm kind-icon"),
        el("span", { class: "name", title: node.name }, node.name),
        counts ? el("span", { class: "badge" }, fmt(hitTotal)) : el("span", { class: "meta" }, fmt(convs.length)));
      container.append(row);
      if (collapsed) return;
      const kids = el("div", { class: "tree-children" });
      for (const ch of node.children.values()) renderNode(ch, kids);
      for (const cid of node.convs) {
        const c = S.convs.get(cid);
        const hits = counts ? counts.get(cid) || 0 : 0;
        if (counts && !hits) continue;
        const on = convVisible(cid);
        kids.append(el("div", { class: `tree-row conv${cid === S.currentConv ? " active" : ""}${on ? "" : " dim"}`, "data-conv": cid, title: `${c.source}` },
          el("span", { class: `cb${on ? " on" : ""}`, "data-conv-toggle": cid }, icon(on ? "check_box" : "check_box_outline_blank", "sm")),
          el("span", { class: "dot", style: { background: c.color } }),
          el("span", { class: "text" }, el("span", { class: "name" }, c.title),
            el("span", { class: "meta" }, `${c.session}${c.index ? " #" + (c.index + 1) : ""} · ${plural(c.n_events, "step")} · ${plural(c.n_tool_calls, "tool call")}${c.n_thoughts ? " · " + plural(c.n_thoughts, "thought") : ""}`)),
          counts ? el("span", { class: "badge", title: "matching paragraphs" }, fmt(hits)) : null));
      }
      container.append(kids);
    };
    for (const ch of root.children.values()) renderNode(ch, frag);
    tree.replaceChildren(frag);
    if (counts && !tree.children.length) tree.append(el("div", { class: "panel-empty" }, "No conversation contains that text."));
  }

  function findNodeByKey(key) {
    const stack = [buildTree()];
    while (stack.length) {
      const n = stack.pop();
      if (n.key === key) return n;
      stack.push(...n.children.values());
    }
    return null;
  }

  function onTreeClick(ev) {
    const t = ev.target;
    const tw = t.closest("[data-twisty]");
    if (tw) {
      const k = tw.dataset.twisty;
      S.collapsed.has(k) ? S.collapsed.delete(k) : S.collapsed.add(k);
      store.set("collapsedTree", [...S.collapsed]);
      renderTree();
      return;
    }
    const tg = t.closest("[data-toggle]");
    if (tg) {
      const node = findNodeByKey(tg.dataset.toggle);
      if (!node) return;
      const convs = descendantConvs(node);
      const allOn = convs.every(convVisible);
      for (const c of convs) allOn ? S.hiddenConvs.add(c) : S.hiddenConvs.delete(c);
      afterConvToggle();
      return;
    }
    const ct = t.closest("[data-conv-toggle]");
    if (ct) {
      const c = ct.dataset.convToggle;
      S.hiddenConvs.has(c) ? S.hiddenConvs.delete(c) : S.hiddenConvs.add(c);
      afterConvToggle();
      return;
    }
    const row = t.closest("[data-conv]");
    if (row) {
      const cid = row.dataset.conv;
      S.currentConv = cid;
      if (S.hiddenConvs.has(cid)) { S.hiddenConvs.delete(cid); afterConvToggle(); }
      renderTree();
      if (S.convSearch && S.convSearch.q) {
        const re = makeRegex(S.convSearch.q);
        const hit = S.convParas.get(cid).find((pid) => { re.lastIndex = 0; return re.test(S.paras.get(pid).t); });
        runSearch(S.convSearch.q, false);
        $("q").value = S.convSearch.q;
        setTab("transcript", { scrollTo: hit });
      } else setTab("transcript");
      fitConversation(cid);
      return;
    }
    const grp = t.closest("[data-key]");
    if (grp) {
      const k = grp.dataset.key;
      S.collapsed.has(k) ? S.collapsed.delete(k) : S.collapsed.add(k);
      store.set("collapsedTree", [...S.collapsed]);
      renderTree();
    }
  }

  function afterConvToggle() {
    applyFilters();
    if (!convVisible(S.currentConv)) S.currentConv = S.convOrder.find(convVisible) || S.currentConv;
    if (network && S.visibleNodes.size) network.fit({ nodes: [...S.visibleNodes], animation: { duration: 400 } });
    renderTree();
    renderLegend();
    renderLayers();
    if (S.search) runSearch(S.search.q, false);
    else renderPanel();
  }

  function runConvSearch(q) {
    q = q.trim();
    $("conv-only").classList.toggle("hidden", !q);
    if (!q) { S.convSearch = null; renderTree(); return; }
    const re = makeRegex(q);
    if (!re) return;
    const counts = new Map();
    for (const p of S.paras.values()) {
      re.lastIndex = 0;
      if (re.test(p.t)) counts.set(p.c, (counts.get(p.c) || 0) + 1);
    }
    for (const c of S.convs.values()) {
      re.lastIndex = 0;
      if (re.test(c.title) || re.test(c.session) || re.test(c.host) || re.test(c.user)) counts.set(c.id, (counts.get(c.id) || 0) + 1);
    }
    S.convSearch = { q, counts };
    renderTree();
  }

  /* ============================================================ legend */
  function renderLegend() {
    const counts = new Map();
    for (const n of S.nodes.values()) {
      if (n.conv && n.conv.length && !n.conv.some(convVisible)) continue;
      const k = kindKey(n);
      counts.set(k, (counts.get(k) || 0) + 1);
    }
    const groups = new Map();
    for (const [key, n] of counts) {
      const k = S.kinds.get(key) || { key, label: key, group: "Other", color: "#9aa0a6" };
      const g = STRUCTURAL.has(key) ? "Conversation structure" : k.group || "Other";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push({ ...k, key, n });
    }
    const order = ["Conversation structure", "Technical", "People & orgs", "Places", "Things", "Time & numbers", "Other"];
    const frag = document.createDocumentFragment();
    for (const g of [...groups.keys()].sort((a, b) => order.indexOf(a) - order.indexOf(b))) {
      const items = groups.get(g).sort((a, b) => b.n - a.n);
      const allOn = items.every((i) => !S.hiddenKinds.has(i.key));
      frag.append(el("div", { class: "legend-group" },
        el("h4", {}, g, el("button", { onclick: () => { for (const i of items) allOn ? S.hiddenKinds.add(i.key) : S.hiddenKinds.delete(i.key); afterKindToggle(); } }, allOn ? "hide all" : "show all")),
        el("div", { class: "chip-row" }, items.map((i) => el("button", {
          class: `chip sm${S.hiddenKinds.has(i.key) ? " off" : ""}`, title: `${i.label} – click to toggle, shift-click to show only this`,
          onclick: (ev) => {
            if (ev.shiftKey) { for (const k of counts.keys()) S.hiddenKinds.add(k); S.hiddenKinds.delete(i.key); for (const s of STRUCTURAL) if (i.key !== s && !STRUCTURAL.has(i.key)) S.hiddenKinds.delete(s); }
            else S.hiddenKinds.has(i.key) ? S.hiddenKinds.delete(i.key) : S.hiddenKinds.add(i.key);
            afterKindToggle();
          },
        }, el("span", { class: "swatch", style: { background: i.color } }), el("span", { class: "label" }, i.label), el("span", { class: "count" }, fmt(i.n)))))));
    }
    $("legend").replaceChildren(frag);
  }
  function afterKindToggle() { applyFilters(); renderLegend(); renderLayers(); if (S.tab === "transcript") renderPanel(); }

  function renderLayers() {
    const counts = new Map();
    for (const id of S.visibleNodes) { const l = S.nodes.get(id).layer; counts.set(l, (counts.get(l) || 0) + 1); }
    $("layers").replaceChildren(...LAYERS.map((l) => el("button", {
      class: `chip${S.hiddenLayers.has(l.key) ? "" : " selected"}`, title: `Show / hide the ${l.label.toLowerCase()} layer`,
      onclick: () => { S.hiddenLayers.has(l.key) ? S.hiddenLayers.delete(l.key) : S.hiddenLayers.add(l.key); applyFilters(); renderLayers(); renderLegend(); },
    }, icon(S.hiddenLayers.has(l.key) ? "visibility_off" : l.icon, "sm"), l.label, el("span", { class: "count" }, fmt(counts.get(l.key) || 0)))));
  }

  function renderStats(st) {
    const box = $("stats");
    if (!st) { box.textContent = "No data yet"; return; }
    let visEdges = 0;
    if (S.data) for (const e of S.data.edges) if (edgeOk(e)) visEdges++;
    box.replaceChildren(
      el("span", {}, `${fmt(S.visibleNodes.size)} / ${fmt(st.nodes)} nodes`),
      el("span", {}, `${fmt(visEdges)} / ${fmt(st.edges)} edges`),
      el("span", {}, `${fmt(st.entities)} entities`),
      el("span", {}, `${fmt(st.paragraphs)} paragraphs`));
  }
  function renderWarnings(ws) {
    $("warnings").replaceChildren(...(ws || []).slice(0, 50).map((w) => el("li", {}, icon("warning", "xs"), el("span", {}, w))));
  }

  /* ============================================================ upload */
  async function upload(files) {
    const zips = [...files].filter((f) => /\.zip$/i.test(f.name));
    if (!zips.length) { snack("Please choose .zip files"); return; }
    const fd = new FormData();
    for (const f of zips) fd.append("files", f, f.name);
    try {
      snack(`Uploading ${plural(zips.length, "file")}…`, null, 0);
      await api("/api/upload", { method: "POST", body: fd });
      showBusy("Analysing transcripts…");
      pollStatus();
    } catch (e) {
      snack("Upload failed: " + e.message, null, 8000);
    }
  }

  function exportMenu(anchor) {
    document.querySelectorAll(".menu").forEach((m) => m.remove());
    const convs = S.convOrder.filter(convVisible).join(",");
    const dark = S.theme === "dark";
    const item = (ic, label, sub, run) => el("button", { onclick: () => { menu.remove(); run(); } }, icon(ic), el("span", { class: "grow" }, label, el("div", { class: "sub" }, sub)));
    const menu = el("div", { class: "menu" },
      item("hub", "Standalone pyvis HTML", "Visible conversations, opens offline", () => (location.href = `/api/export/pyvis?convs=${encodeURIComponent(convs)}&dark=${dark}`)),
      item("account_tree", "GraphML", "For Gephi, yEd, Cytoscape", () => (location.href = `/api/export/graphml?convs=${encodeURIComponent(convs)}`)),
      item("download", "PNG snapshot", "Current view of the canvas", () => {
        const canvas = $("graph").querySelector("canvas");
        const out = document.createElement("canvas");
        out.width = canvas.width; out.height = canvas.height;
        const cx = out.getContext("2d");
        cx.fillStyle = GC.surface; cx.fillRect(0, 0, out.width, out.height); cx.drawImage(canvas, 0, 0);
        const a = el("a", { href: out.toDataURL("image/png"), download: "synthsift-graph.png" });
        document.body.append(a); a.click(); a.remove();
      }));
    const r = anchor.getBoundingClientRect();
    menu.style.top = r.bottom + 4 + "px";
    menu.style.right = Math.max(8, window.innerWidth - r.right) + "px";
    document.body.append(menu);
    setTimeout(() => document.addEventListener("click", function close(e) { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", close); } }), 0);
  }

  /* ============================================================ wiring */
  function wireUI() {
    const stage = $("stage");
    stage.addEventListener("pointermove", (e) => { S.pointer = { x: e.clientX, y: e.clientY }; });
    stage.addEventListener("pointerleave", hideTip);

    const q = $("q");
    q.addEventListener("input", debounce(() => runSearch(q.value), 220));
    q.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        runSearch(q.value);
        if (S.search && S.search.nodeIds.size && network) network.fit({ nodes: [...S.search.nodeIds], animation: { duration: 500 } });
      } else if (e.key === "Escape") { q.value = ""; runSearch(""); q.blur(); }
    });
    $("q-clear").addEventListener("click", (e) => { e.preventDefault(); q.value = ""; runSearch(""); });

    const cq = $("conv-q");
    cq.addEventListener("input", debounce(() => runConvSearch(cq.value), 220));
    $("conv-only").addEventListener("click", (e) => {
      e.preventDefault();
      if (!S.convSearch) return;
      for (const c of S.convOrder) (S.convSearch.counts.get(c) ? S.hiddenConvs.delete(c) : S.hiddenConvs.add(c));
      afterConvToggle();
      snack(`Showing ${plural(S.convSearch.counts.size, "matching conversation")}`);
    });
    $("conv-all").addEventListener("click", () => { S.hiddenConvs.clear(); afterConvToggle(); });
    $("conv-none").addEventListener("click", () => { for (const c of S.convOrder) S.hiddenConvs.add(c); afterConvToggle(); });
    $("conv-expand").addEventListener("click", () => {
      const anyOpen = document.querySelectorAll("#tree .tree-children").length > 0 && S.collapsed.size === 0;
      S.collapsed.clear();
      if (anyOpen) {
        const stack = [buildTree()];
        while (stack.length) { const n = stack.pop(); if (n.key !== "root" && n.kind !== "harness") S.collapsed.add(n.key); stack.push(...n.children.values()); }
      }
      store.set("collapsedTree", [...S.collapsed]);
      $("conv-expand").querySelector(".msi").textContent = anyOpen ? "unfold_more" : "unfold_less";
      renderTree();
    });
    $("tree").addEventListener("click", onTreeClick);
    $("legend-reset").addEventListener("click", () => { S.hiddenKinds.clear(); S.hiddenLayers.clear(); applyFilters(); renderLegend(); renderLayers(); });

    for (const b of $("layout-toggle").querySelectorAll("button")) b.addEventListener("click", () => setLayout(b.dataset.layout));
    $("z-in").addEventListener("click", () => network && network.moveTo({ scale: network.getScale() * 1.3, animation: { duration: 200 } }));
    $("z-out").addEventListener("click", () => network && network.moveTo({ scale: network.getScale() / 1.3, animation: { duration: 200 } }));
    $("z-fit").addEventListener("click", () => network && network.fit({ animation: { duration: 400 } }));
    $("t-labels").addEventListener("click", () => { S.labels = !S.labels; store.set("labels", S.labels); $("t-labels").classList.toggle("on", S.labels); network && network.redraw(); });
    $("t-physics").addEventListener("click", () => setPhysics(!S.physics));

    const file = $("file");
    for (const id of ["btn-upload", "empty-upload"]) $(id).addEventListener("click", () => file.click());
    file.addEventListener("change", () => { upload(file.files); file.value = ""; });
    $("empty-samples").addEventListener("click", async () => {
      try { await api("/api/samples", { method: "POST" }); showBusy("Loading sample transcripts…"); pollStatus(); } catch (e) { snack(e.message); }
    });
    let dragDepth = 0;
    window.addEventListener("dragenter", (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { dragDepth++; $("drop").classList.add("show"); } });
    window.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $("drop").classList.remove("show"); });
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; $("drop").classList.remove("show"); if (e.dataTransfer?.files?.length) upload(e.dataTransfer.files); });

    $("btn-export").addEventListener("click", (e) => { e.stopPropagation(); if (S.data) exportMenu(e.currentTarget); else snack("Nothing to export yet"); });
    $("btn-theme").addEventListener("click", () => {
      const cur = store.get("theme", "auto");
      const next = S.theme === "dark" ? "light" : "dark";
      S.theme = SS.applyTheme(cur === "auto" && next === SS.effectiveTheme("auto") ? "auto" : next);
      $("btn-theme").querySelector(".msi").textContent = S.theme === "dark" ? "light_mode" : "dark_mode";
      GC = graphColors();
      restyleEdges();
      network && network.redraw();
    });
    $("btn-left").addEventListener("click", () => { const l = $("layout").classList.toggle("left-closed"); store.set("leftClosed", l); });
    $("btn-right").addEventListener("click", () => {
      const r = $("layout").classList.toggle("right-closed");
      store.set("rightClosed", r);
      $("btn-right").querySelector(".msi").textContent = r ? "right_panel_open" : "right_panel_close";
    });

    for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => setTab(b.dataset.tab));
    $("panel-body").addEventListener("click", onPanelClick);
    $("underline-toggle").addEventListener("click", () => {
      S.underline = !S.underline; store.set("underline", S.underline);
      $("underline-toggle").classList.toggle("on", S.underline);
      $("panel-body").classList.toggle("no-underline", !S.underline);
    });
    const setCtx = (which, v) => {
      v = Math.max(0, Math.min(20, v | 0));
      if (which === "before") { S.ctxBefore = v; $("ctx-before").value = v; store.set("ctxBefore", v); }
      else { S.ctxAfter = v; $("ctx-after").value = v; store.set("ctxAfter", v); }
      if (S.tab === "matches") renderMatches();
    };
    for (const b of document.querySelectorAll("[data-step]")) b.addEventListener("click", (e) => {
      e.preventDefault();
      const [which, d] = b.dataset.step.split(":");
      setCtx(which, (which === "before" ? S.ctxBefore : S.ctxAfter) + Number(d));
    });
    $("ctx-before").addEventListener("change", (e) => setCtx("before", e.target.value));
    $("ctx-after").addEventListener("change", (e) => setCtx("after", e.target.value));

    // resizable transcript panel
    const split = $("splitter");
    split.addEventListener("pointerdown", (e) => {
      split.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const w = Math.max(280, Math.min(window.innerWidth - 480, window.innerWidth - ev.clientX - 12));
        document.documentElement.style.setProperty("--right-w", w + "px");
      };
      const up = () => {
        split.removeEventListener("pointermove", move);
        split.removeEventListener("pointerup", up);
        store.set("rightWidth", parseInt(getComputedStyle(document.documentElement).getPropertyValue("--right-w"), 10));
      };
      split.addEventListener("pointermove", move);
      split.addEventListener("pointerup", up);
    });

    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea, select")) return;
      if (e.key === "/") { e.preventDefault(); $("q").focus(); }
      else if (e.key === "Escape") clearSelection();
      else if (e.key === "f" && network) network.fit({ animation: { duration: 400 } });
    });
    window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (store.get("theme", "auto") === "auto") { S.theme = SS.effectiveTheme("auto"); GC = graphColors(); restyleEdges(); network && network.redraw(); }
    });
  }

  boot();
  window.SynthSift = { S, get network() { return network; }, selectNode, runSearch };
})();
