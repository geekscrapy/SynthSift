/* Shared helpers for every page: API, DOM, theme, formatting, snackbar, menus, dialogs, the loading screen, the
 * module inspector, and the graph / annotation model the graph page shares with the Nodes and Timeline pages. */
"use strict";

const SS = (() => {
  const store = {
    get(key, fallback) {
      try {
        const v = localStorage.getItem("synthsift." + key);
        return v === null ? fallback : JSON.parse(v);
      } catch (e) {
        return fallback;
      }
    },
    set(key, value) {
      try { localStorage.setItem("synthsift." + key, JSON.stringify(value)); } catch (e) { /* private mode */ }
    },
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
      ...opts,
      body: opts.body && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body,
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (e) { /* not json */ }
      throw new Error(msg);
    }
    return res.json();
  }

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
      else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
      else if (k === "html") e.innerHTML = v;
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      e.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return e;
  }

  const icon = (name, cls = "") => el("span", { class: `msi ${cls}` }, name);

  /* ------------------------------------------------------------- theme */
  function effectiveTheme(pref) {
    if (pref === "light" || pref === "dark") return pref;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function applyTheme(pref) {
    if (pref === "light" || pref === "dark") document.documentElement.dataset.theme = pref;
    else delete document.documentElement.dataset.theme;
    store.set("theme", pref || "auto");
    return effectiveTheme(pref);
  }
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  /* ---------------------------------------------------------- snackbar */
  let snackTimer = null;
  function snack(text, action = null, ms = 5000) {
    const bar = document.getElementById("snackbar");
    if (!bar) return;
    document.getElementById("snack-text").textContent = text;
    const btn = document.getElementById("snack-action");
    btn.classList.toggle("hidden", !action);
    if (action) {
      btn.textContent = action.label;
      btn.onclick = () => { bar.classList.remove("show"); action.run(); };
    }
    bar.classList.add("show");
    clearTimeout(snackTimer);
    if (ms) snackTimer = setTimeout(() => bar.classList.remove("show"), ms);
  }

  const debounce = (fn, ms) => {
    let t = null;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  };

  /** harness metadata worth showing on a conversation: [icon, text, title, extra class] */
  const META_CHIPS = [
    ["archive", (m) => m.archive && `${m.archive} session`, "Recovered from an archive of a deleted / reset session", "warn"],
    ["account_tree", (m) => m.subagent && `sub-agent${m.agent_id ? " " + m.agent_id : ""}`, "Runs as a sub-agent of another session"],
    ["forum", (m) => m.channel && `${m.channel}${m.chat_type ? " · " + m.chat_type : ""}`, "Messaging channel"],
    ["key", (m) => m.session_key, "Session key"],
    ["folder", (m) => m.cwd, "Working directory"],
    ["fork_right", (m) => m.git_branch, "Git branch"],
  ];
  /** the chips; with `onPick(get, value)` they are buttons (e.g. to show every session with the same value) */
  function metaChips(meta, onPick = null) {
    const out = [];
    for (const [ic, get, title, cls] of META_CHIPS) {
      const v = meta && get(meta);
      if (!v) continue;
      const c = `tag${cls ? " " + cls : ""}`;
      out.push(onPick
        ? el("button", { class: c + " pill-btn", title: `${title}: ${v}\nClick to show every session with the same`, onclick: () => onPick(get, v) }, icon(ic, "xs"), String(v))
        : el("span", { class: c, title: `${title}: ${v}` }, icon(ic, "xs"), String(v)));
    }
    return out;
  }

  /* -------------------------------------------------------------- text */
  const fmt = (n) => Number(n).toLocaleString();
  const plural = (n, word, many) => `${fmt(n)} ${n === 1 ? word : many || word + "s"}`;
  const secs = (s) => (s >= 90 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : s >= 10 ? `${Math.round(s)} s` : `${(s || 0).toFixed(1)} s`);
  function fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    return isNaN(d) ? String(ts) : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function fmtDay(ts) {
    const d = new Date(ts);
    return isNaN(d) ? "Unknown date" : d.toLocaleDateString([], { weekday: "short", year: "numeric", month: "short", day: "numeric" });
  }
  /** "Mar 3, 10:00 – 11:00" for a time window (either end may be empty) */
  function fmtRange(from, to) {
    const a = from ? new Date(from) : null, b = to ? new Date(to) : null;
    const sameDay = a && b && a.toDateString() === new Date(b - 1).toDateString();
    const end = b && sameDay ? b.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : b ? fmtTime(to) : "";
    return a && b ? `${fmtTime(from)} – ${end}` : a ? `from ${fmtTime(from)}` : b ? `until ${fmtTime(to)}` : "";
  }
  /** ISO time <-> the local "YYYY-MM-DDTHH:MM" of a datetime-local input */
  const isoToLocalInput = (iso) => {
    const d = iso ? new Date(iso) : null;
    if (!d || isNaN(d)) return "";
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  const localInputToIso = (v) => (v && !isNaN(new Date(v)) ? new Date(v).toISOString() : "");

  // The workspace scope shared by every page (and kept in localStorage "convFilter"): host, user, agent (harness),
  // conversation and a time window (from / to, ISO times, "to" exclusive). Pages also accept these as URL parameters.
  const SCOPE_KEYS = ["host", "user", "harness", "conv", "from", "to"];
  const emptyScope = () => Object.fromEntries(SCOPE_KEYS.map((k) => [k, ""]));
  /** `filter` with `patch` applied; a broader key clears the narrower ones it does not set (host > user > agent > conversation) */
  function narrowScope(filter, patch) {
    const f = { ...filter, ...patch };
    if ("host" in patch) { f.user = patch.user || ""; f.harness = patch.harness || ""; }
    else if ("user" in patch) f.harness = patch.harness || "";
    if (("host" in patch || "user" in patch || "harness" in patch) && !("conv" in patch)) f.conv = "";
    return f;
  }
  /** scope values given in a page's URL (?from=…&to=…&host=…), or null */
  function scopeFromURL(params) {
    const patch = {};
    for (const k of SCOPE_KEYS) if (params.has(k)) patch[k] = params.get(k);
    return Object.keys(patch).length ? patch : null;
  }
  /** a page URL with scope and page parameters (empty values left out) */
  function pageURL(path, params = {}) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, Array.isArray(v) ? v.join(",") : v);
    const s = q.toString();
    return s ? `${path}?${s}` : path;
  }
  /** a search box's pattern: /regex/flags, or plain text matched case-insensitively; null when empty or invalid */
  function makeRegex(q, global = false) {
    const m = q.match(/^\/(.+)\/([a-z]*)$/);
    try {
      if (m) return new RegExp(m[1], m[2].replace("g", "") + (global ? "g" : ""));
      return q ? new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), global ? "gi" : "i") : null;
    } catch (e) {
      return null;
    }
  }
  const strHash = (s) => {
    let h = 0;
    for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return h;
  };

  /* --------------------------------------------------------- downloads */
  function download(href, name) {
    const a = el("a", { href, download: name });
    document.body.append(a); a.click(); a.remove();
  }
  function downloadCSV(name, header, rows) {
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const text = [header.map(q).join(","), ...rows.map((r) => r.map(q).join(","))].join("\n");
    download(URL.createObjectURL(new Blob([text], { type: "text/csv" })), name);
  }

  /* ------------------------------------------------------------- menus */
  function closeMenus() { document.querySelectorAll(".menu").forEach((m) => m.remove()); }
  /** show a menu at x, y (kept on screen); a mouse-down anywhere else closes it */
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

  /* ------------------------------------------------------------ dialog */
  function dialog({ title, iconName = "", body, wide = false, onClose = null }) {
    const close = () => { root.remove(); document.removeEventListener("keydown", onKey); if (onClose) onClose(); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const card = el("div", { class: `dialog${wide ? " wide" : ""}`, role: "dialog", "aria-label": title },
      el("div", { class: "dialog-head" }, iconName ? icon(iconName) : null, el("div", { class: "grow dialog-title" }, title),
        el("button", { class: "icon-btn sm", title: "Close", onclick: close }, icon("close", "sm"))),
      el("div", { class: "dialog-body" }, body));
    const root = el("div", { class: "dialog-scrim", onclick: (e) => { if (e.target === root) close(); } }, card);
    document.body.append(root);
    document.addEventListener("keydown", onKey);
  }

  /* ---------------------------------------------------- loading screen */
  // Shows the pipeline while the server works: stages, and every enrichment
  // module with its own progress.  Blocking (full screen) when there is nothing
  // to look at yet, otherwise a card in the corner that can be minimised.
  const loading = (() => {
    let root = null;
    let errorShown = "";
    const STATE_ICON = { done: "check_circle", cached: "check_circle", skipped: "remove", error: "error", waiting: "radio_button_unchecked" };
    const STAGE_WEIGHT = { ingest: 0.1, segment: 0.1, enrich: 0.7, graph: 0.1 };

    function overall(st) {
      let total = 0;
      for (const s of st.stages || []) {
        const w = STAGE_WEIGHT[s.name] || 0.1;
        if (s.state === "done" || s.state === "skipped") total += w;
        else if (s.state === "running") {
          let frac = st.stage === s.name ? st.progress || 0 : 0;
          if (s.name === "enrich" && (st.steps || []).length) {
            frac = st.steps.reduce((a, m) => a + (m.state === "done" || m.state === "cached" ? 1 : m.progress || 0), 0) / st.steps.length;
          }
          total += w * frac;
        }
      }
      return Math.max(0, Math.min(1, total));
    }

    function stateIcon(state) {
      return state === "running" ? el("span", { class: "spinner sm" }) : icon(STATE_ICON[state] || "radio_button_unchecked", `xs st-ico ${state}`);
    }

    function stepRow(m) {
      const pct = m.state === "done" || m.state === "cached" ? 100 : Math.round((m.progress || 0) * 100);
      const right = m.state === "running" ? m.message : m.state === "cached" ? "up to date" : m.state === "done" ? secs(m.seconds) : m.state === "waiting" ? "waiting" : m.message;
      return el("li", { class: `lc-step ${m.state}` },
        stateIcon(m.state),
        el("span", { class: "grow lc-name" }, m.label, el("span", { class: `kind-chip ${m.kind}` }, m.kind),
          m.workers && m.state === "running" && m.workers !== "thread" ? el("span", { class: "muted lc-workers" }, m.workers) : null),
        el("span", { class: "muted lc-val" }, right || ""),
        m.state === "running" && m.total ? el("div", { class: "mini-bar" }, el("div", { class: "bar", style: { width: pct + "%" } })) : null);
    }

    function render(st, blocking) {
      if (!root) {
        root = el("div", { class: "loading" });
        document.body.append(root);
      }
      const min = store.get("loadingMin", false) && !blocking;
      root.className = `loading${blocking ? " blocking" : " docked"}${min ? " min" : ""}${st.state === "error" ? " error" : ""}`;
      const pct = Math.round(overall(st) * 100);
      const running = (st.stages || []).find((s) => s.state === "running");
      const elapsed = st.elapsed ? secs(st.elapsed) : "";
      if (min) {
        root.replaceChildren(el("button", { class: "loading-pill", title: "Show progress", onclick: () => { store.set("loadingMin", false); render(st, blocking); } },
          el("span", { class: "spinner sm" }), el("span", {}, running ? running.label : "Working"), el("b", {}, pct + "%")));
        return;
      }
      const title = st.state === "error" ? "Processing failed" : "Processing transcripts";
      const head = el("div", { class: "lc-head" },
        st.state === "error" ? icon("error", "lc-err") : el("span", { class: "spinner" }),
        el("div", { class: "grow" }, el("div", { class: "lc-title" }, title),
          el("div", { class: "lc-sub" }, st.state === "error" ? st.error : running ? running.label : "Starting…")),
        el("span", { class: "lc-time muted" }, st.state === "error" ? "" : elapsed),
        st.state === "error"
          ? el("button", { class: "icon-btn sm", title: "Dismiss", onclick: () => hide() }, icon("close", "sm"))
          : blocking ? null : el("button", { class: "icon-btn sm", title: "Minimise", onclick: () => { store.set("loadingMin", true); render(st, blocking); } }, icon("close_fullscreen", "sm")));
      const bar = el("div", { class: "lc-bar" }, el("div", { class: "bar", style: { width: pct + "%" } }));
      const stages = el("ol", { class: "lc-stages" });
      for (const s of st.stages || []) {
        const li = el("li", { class: `lc-stage ${s.state}` },
          el("div", { class: "lc-stage-row" }, stateIcon(s.state), el("span", { class: "grow" }, s.label),
            el("span", { class: "muted lc-val" }, s.state === "running" ? s.message || "" : s.state === "done" ? secs(s.seconds) : s.state === "skipped" ? "not needed" : "")));
        if (s.name === "enrich" && (st.steps || []).length && s.state !== "skipped") li.append(el("ul", { class: "lc-steps" }, st.steps.map(stepRow)));
        stages.append(li);
      }
      root.replaceChildren(el("div", { class: "loading-card" }, head, bar, stages,
        st.state === "error" && st.message ? el("pre", { class: "lc-trace" }, st.message) : null));
    }

    function hide() { if (root) { root.remove(); root = null; } }

    /** st: /api/status.  blocking: nothing else to show yet (full screen). */
    function update(st, { blocking = false } = {}) {
      if (st.state === "running") { errorShown = ""; render(st, blocking); return; }
      if (st.state === "error") {
        if (errorShown === st.error) return; // dismissed or already shown
        errorShown = st.error;
        render(st, false);
        return;
      }
      hide();
    }
    return { update };
  })();

  /* --------------------------------------------------- module inspector */
  // What every enrichment module stored for some paragraphs (click a turn's data button).
  async function inspect(pids, { title = "Enrichment", paras = null } = {}) {
    const body = el("div", { class: "inspector" }, el("div", { class: "muted" }, "Loading…"));
    dialog({ title: `${title} – what the modules extracted`, iconName: "data_object", body, wide: true });
    const parts = [];
    for (const [i, pid] of pids.entries()) {
      let d;
      try { d = await api(`/api/enrichment/${encodeURIComponent(pid)}`); } catch (e) { parts.push(el("div", { class: "muted" }, `${pid}: ${e.message}`)); continue; }
      const p = paras && paras.get(pid);
      const mods = d.modules.filter((m) => m.tables.length);
      const ct = mods.find((m) => m.name === "content_type")?.tables[0]?.rows[0];
      const ts = mods.find((m) => m.name === "text_stats")?.tables[0];
      const stat = (k) => ts && ts.rows[0] ? ts.rows[0][ts.columns.indexOf(k)] : null;
      const sec = el("section", { class: "insp-para" },
        el("div", { class: "insp-head" }, el("b", {}, `Paragraph ${i + 1}`),
          ct ? el("span", { class: "tag" }, icon("category", "xs"), ct[0]) : null,
          ...(ct && ct[1] ? ct[1].map((f) => el("span", { class: "tag warn" }, icon("key", "xs"), f)) : []),
          ts ? el("span", { class: "muted" }, `${fmt(stat("words"))} words · entropy ${stat("entropy")}`) : null,
          el("span", { class: "grow" }), el("span", { class: "muted mono", title: "Content hash – every module row is keyed by it" }, d.hash)),
        p ? el("div", { class: `insp-text${p.code ? " mono" : ""}` }, p.t.length > 600 ? p.t.slice(0, 600) + "…" : p.t) : null);
      for (const m of mods) {
        for (const t of m.tables) {
          const n = t.rows.length;
          const det = el("details", { class: "insp-table", open: n > 0 && n <= 12 && m.kind !== "feature" && m.kind !== "label" ? true : undefined },
            el("summary", {}, el("span", { class: `kind-chip ${m.kind}` }, m.kind), el("b", {}, m.label), el("span", { class: "mono muted" }, t.name), el("span", { class: "muted" }, plural(n, "row"))));
          if (n) {
            const rows = t.rows.slice(0, 300);
            det.append(...[el("div", { class: "insp-scroll" }, el("table", { class: "data-table compact" },
              el("thead", {}, el("tr", {}, t.columns.map((c) => el("th", {}, c)))),
              el("tbody", {}, rows.map((r) => el("tr", {}, r.map((v) => el("td", { class: typeof v === "number" ? "num" : "" },
                v === null ? "" : Array.isArray(v) ? v.join(", ") : typeof v === "boolean" ? (v ? "✓" : "") : String(v)))))))),
            n > rows.length ? el("div", { class: "muted" }, `… ${fmt(n - rows.length)} more (export the module's CSV on the Settings page)`) : null].filter(Boolean));
          }
          sec.append(det);
        }
      }
      parts.push(sec);
    }
    body.replaceChildren(...parts);
  }

  /* ------------------------------------------------ severities and kinds */
  const SEV_ORDER = ["info", "low", "medium", "high", "critical"];
  const SEV_COLOR = { critical: "#A50E0E", high: "#D93025", medium: "#E37400", low: "#B08800", info: "#5F6368" };
  const sevRank = (s) => SEV_ORDER.indexOf(s);
  const worstSeverity = (fs) => fs.reduce((a, f) => (sevRank(f.severity) > sevRank(a) ? f.severity : a), "");

  // "kinds" are the node types and entity categories, each with a label, group, icon and colour
  const STRUCTURAL = new Set(["conversation", "user", "assistant", "system", "thought", "tool_call", "tool_arg", "tool_result", "tool_hub"]);
  const KIND_GROUPS = ["Conversation structure", "Custom", "Technical", "People & orgs", "Places", "Things", "Time & numbers", "Other"];
  const LAYERS = [
    { key: "thought", label: "Thoughts", icon: "psychology" },
    { key: "dialogue", label: "Dialogue", icon: "forum" },
    { key: "action", label: "Actions", icon: "build" },
    { key: "entity", label: "Entities", icon: "hub" },
  ];
  const ROLE_ICON = { user: "person", assistant: "smart_toy", system: "settings", thought: "psychology", tool_call: "build", tool_result: "output" };
  // categories invented in a custom vocabulary (Settings → Modules → spaCy NLP) get a stable colour of their own
  const EXTRA_COLORS = ["#7B1FA2", "#00897B", "#C0CA33", "#6D4C41", "#3949AB", "#D81B60", "#00ACC1", "#F4511E"];
  const kindKey = (n) => (n.type === "entity" ? n.category : n.type);
  /** fill a kinds map from the graph payload, with the colours picked in Settings */
  function loadKinds(kinds, g, settings) {
    kinds.clear();
    for (const k of [...g.kinds.node_types, ...g.kinds.categories]) kinds.set(k.key, { ...k, color: settings["color." + k.key] || k.color });
  }

  /* --------------------------------------------------------- graph model */
  // Lookups and analyst annotations over a page's loaded graph G: the graph page's state, or the workspace the
  // Nodes and Timeline pages share. G holds kinds, convs, events, paras and nodes (Maps), filter, hiddenConvs,
  // annotations and tags. Annotation targets are "conv:<cid>", "event:<event id>" and "term:<entity node id>".
  //   onSaved()    runs after an annotation was saved
  //   promptTag()  asks for a new tag's name in the tag menu (and may create the tag); resolves to it or null
  function model(G, { onSaved, promptTag }) {
    /* kinds */
    function kind(key) {
      let k = G.kinds.get(key);
      if (!k) G.kinds.set(key, (k = { key, label: key.replace(/_/g, " "), group: "Custom", icon: "label", color: EXTRA_COLORS[strHash(key) % EXTRA_COLORS.length] }));
      return k;
    }
    const kindOf = (n) => kind(kindKey(n));
    const kindGroup = (key) => (STRUCTURAL.has(key) ? "Conversation structure" : (G.kinds.get(key) || {}).group || "Other");
    const avatarHTML = (type) => `<span class="avatar" style="background:${esc((G.kinds.get(type) || {}).color || "#80868b")}"><span class="msi">${ROLE_ICON[type] || "chat"}</span></span>`;

    /* conversation scope */
    function convMatchesFilter(cid) {
      const c = G.convs.get(cid);
      if (!c) return false;
      const f = G.filter;
      return (!f.host || c.host === f.host) && (!f.user || c.user === f.user) && (!f.harness || c.harness === f.harness)
        && (!f.conv || c.id === f.conv);
    }
    const convVisible = (cid) => !G.hiddenConvs.has(cid) && convMatchesFilter(cid);

    /* time window (filter.from / filter.to): turns outside it, and nodes seen only outside it, are out of scope */
    let win = { key: null };
    function windowState() {
      const f = G.filter, key = `${f.from || ""}|${f.to || ""}|${G.events.size}`;
      if (win.key !== key) {
        win = { key, on: !!(f.from || f.to), lo: f.from ? Date.parse(f.from) : -Infinity, hi: f.to ? Date.parse(f.to) : Infinity, convs: null };
      }
      return win;
    }
    const windowOn = () => windowState().on;
    function inWindow(ts) {
      const w = windowState();
      if (!w.on) return true;
      const t = ts ? Date.parse(ts) : NaN;
      return t >= w.lo && t < w.hi;
    }
    const eventVisible = (evId) => { const ev = G.events.get(evId); return !!ev && convVisible(ev.c) && inWindow(ev.ts); };
    const paraVisible = (pid) => { const p = G.paras.get(pid); return !!p && eventVisible(p.e); };
    function nodeInWindow(n) {
      const w = windowState();
      if (!w.on) return true;
      if (n.type === "conversation") {
        if (!w.convs) { w.convs = new Set(); for (const ev of G.events.values()) if (inWindow(ev.ts)) w.convs.add(ev.c); }
        return w.convs.has(n.conv[0]);
      }
      if (n.type === "tool_arg") return inWindow((G.events.get(n.event) || {}).ts);
      if (G.events.has(n.id)) return inWindow(G.events.get(n.id).ts);
      if (n.occ && n.occ.length) return n.occ.some(([pid]) => inWindow((G.events.get((G.paras.get(pid) || {}).e) || {}).ts));
      return true; // tool hubs stay while any of their calls is visible
    }

    /* annotation targets */
    function targetOf(nodeId) {
      const n = G.nodes.get(nodeId);
      if (!n) return null;
      if (n.type === "conversation") return nodeId;
      if (n.type === "entity") return "term:" + nodeId;
      if (n.type === "tool_arg") return "event:" + n.event;
      if (n.type === "tool_hub") return null;
      return "event:" + nodeId;
    }
    const annOf = (t) => (t && G.annotations[t]) || null;
    const tagsFor = (t) => (annOf(t) || {}).tags || [];
    const tagInfo = (name) => G.tags.find((t) => t.name === name) || { name, color: "#5F6368", icon: "sell" };
    // tags that apply to a node: its own, plus its conversation's for structural nodes
    function nodeTags(nodeId) {
      const own = tagsFor(targetOf(nodeId));
      const n = G.nodes.get(nodeId);
      if (n && n.type !== "entity" && n.type !== "conversation" && n.conv && n.conv[0]) {
        const ct = tagsFor("conv:" + n.conv[0]);
        if (ct.length) return [...new Set([...own, ...ct])];
      }
      return own;
    }
    /** earliest / latest timestamp a node appears at */
    function seenRange(nodeId) {
      const n = G.nodes.get(nodeId);
      let lo = null, hi = null;
      for (const [pid] of (n && n.occ) || []) {
        const ts = G.events.get(G.paras.get(pid)?.e)?.ts;
        if (!ts) continue;
        if (!lo || ts < lo) lo = ts;
        if (!hi || ts > hi) hi = ts;
      }
      return [lo, hi];
    }
    function labelFor(target) {
      if (target.startsWith("conv:")) { const c = G.convs.get(target.slice(5)); return c ? c.title : target; }
      if (target.startsWith("event:")) {
        const ev = G.events.get(target.slice(6));
        if (!ev) return target;
        const first = ev.p.length ? G.paras.get(ev.p[0]).t.replace(/\s+/g, " ").slice(0, 90) : "";
        return `${ev.label}${first ? " – " + first : ""}`;
      }
      const n = G.nodes.get(target.slice(5));
      return n ? n.label : target.slice(5).replace(/^ent:/, "");
    }
    function convFor(target) {
      if (target.startsWith("conv:")) return target.slice(5);
      if (target.startsWith("event:")) { const ev = G.events.get(target.slice(6)); return ev ? ev.c : null; }
      const n = G.nodes.get(target.slice(5));
      return n && n.conv && n.conv.length ? n.conv[0] : null;
    }
    function tsFor(target) {
      if (target.startsWith("conv:")) { const c = G.convs.get(target.slice(5)); return c ? c.started_at || null : null; }
      if (target.startsWith("event:")) { const ev = G.events.get(target.slice(6)); return ev ? ev.ts || null : null; }
      return seenRange(target.slice(5))[0];
    }

    /* saving */
    async function loadAnnotations() {
      try {
        const r = await api("/api/annotations");
        G.annotations = r.annotations || {};
        G.tags = r.tags || [];
      } catch (e) { /* keep previous */ }
    }
    /** store a target's tags and comment (undefined: keep the comment); throws when the server refuses */
    async function putAnnotation(target, tags, comment) {
      const prev = annOf(target);
      const body = { target, tags, comment: comment ?? (prev ? prev.comment : ""), label: labelFor(target), conv: convFor(target), ts: tsFor(target) };
      const r = await api("/api/annotations", { method: "PUT", body });
      if (r.annotation) G.annotations[target] = r.annotation; else delete G.annotations[target];
      G.tags = r.tags || G.tags;
    }
    async function saveAnnotation(target, tags, comment) {
      try {
        await putAnnotation(target, tags, comment);
        onSaved();
      } catch (e) {
        snack("Could not save: " + e.message);
      }
    }
    function toggleTag(target, tag) {
      const cur = new Set(tagsFor(target));
      cur.has(tag) ? cur.delete(tag) : cur.add(tag);
      return saveAnnotation(target, [...cur]);
    }
    /** ask for a name and create a custom tag in the next palette colour; resolves to the name or null */
    async function newTag() {
      const name = (prompt("New tag name") || "").trim().toLowerCase();
      if (!name) return null;
      const color = ["#1A73E8", "#9334E6", "#12B5CB", "#E52592", "#188038", "#B06000"][G.tags.length % 6];
      try {
        G.tags = (await api("/api/tags", { method: "POST", body: { name, color } })).tags;
        return name;
      } catch (e) { snack(e.message); return null; }
    }

    /* tag views */
    /** tag chips as HTML; `inherited` ones (a set) are drawn outlined */
    const tagChipsHTML = (tags, inherited = null) => tags.map((t) => (inherited && inherited.has(t)
      ? `<span class="tag-chip inherited" style="--tag:${esc(tagInfo(t).color)}" title="Inherited from the session">${esc(t)}</span>`
      : `<span class="tag-chip" style="--tag:${esc(tagInfo(t).color)}">${esc(t)}</span>`)).join("");
    /** a target's tags and comment, read-only; null when it has neither (tagging is done from the right-click menu) */
    function annotationView(target) {
      const a = target && annOf(target);
      if (!a || (!a.tags.length && !a.comment)) return null;
      return el("div", { class: "ann-view" }, a.tags.length ? el("div", { class: "tag-row", html: tagChipsHTML(a.tags) }) : null,
        a.comment ? el("div", { class: "comment-note" }, icon("comment"), a.comment) : null);
    }

    /* right-click menu */
    /** the conversations and time span [first, last] of a node id or tag target */
    function scopeOf(ref) {
      const id = ref.replace(/^(term|event):/, "");
      if (id.startsWith("conv:")) {
        const cid = id.slice(5);
        let lo = null, hi = null;
        for (const ev of G.events.values()) {
          if (ev.c !== cid || !ev.ts) continue;
          if (!lo || ev.ts < lo) lo = ev.ts;
          if (!hi || ev.ts > hi) hi = ev.ts;
        }
        return { convs: G.convs.has(cid) ? [cid] : [], span: [lo, hi] };
      }
      const n = G.nodes.get(id);
      const ev = G.events.get(n && n.event ? n.event : id);
      if (ev) return { convs: [ev.c], span: [ev.ts, ev.ts] };
      return { convs: (n && n.conv) || [], span: n ? seenRange(id) : [null, null] };
    }
    /** every session a term appears in (a term is one node per conversation when not merged across them) */
    function termSessions(n) {
      const label = n.label.toLowerCase(), out = new Set(n.conv || []);
      for (const m of G.nodes.values()) if (m.type === "entity" && m !== n && m.label.toLowerCase() === label) for (const c of m.conv || []) out.add(c);
      return [...out].filter((c) => G.convs.has(c));
    }
    const MENU_KIND = { conv: "Session", event: "Turn", term: "Term" };
    const FILTER_ROWS = [["host", "computer", "Host"], ["user", "person", "User"], ["harness", "terminal", "Agent"], ["conv", "forum", "Session"]];
    const dayStart = (ts, add = 0) => { const d = new Date(ts); return new Date(d.getFullYear(), d.getMonth(), d.getDate() + add).toISOString(); };
    const shortDay = (ts) => new Date(ts).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
    /** right-click menu for a node or table row (`ref`: node id or tag target): "Filter on" its host, user, agent,
     *  session or days (for a term also "Show all sessions containing it"), then tag checkboxes, "New tag…" and a
     *  comment. `target` is null for things that cannot be tagged (tool hubs); `onFilter(patch, what)` narrows the
     *  shared scope, `onSessions(convIds, what)` shows only those sessions. */
    function itemMenu(ref, x, y, { target = null, onFilter, onSessions }) {
      closeMenus();
      const n = G.nodes.get(ref);
      const label = n ? n.label : target ? labelFor(target) : ref;
      const what = n ? kindOf(n).label : MENU_KIND[ref.split(":", 1)[0]] || "Item";
      const menu = el("div", { class: "menu tag-menu", role: "menu" });
      const filterItem = (ic, key, text, patch, on) => el("button", {
        class: "im-filter", role: "menuitem", disabled: on, title: on ? "Already filtered on this" : `Show only ${key.toLowerCase()} ${text}`,
        onclick: () => { menu.remove(); onFilter(patch, `${key.toLowerCase()} “${text}”`); },
      }, icon(on ? "check" : ic), el("span", { class: "im-key" }, key), el("span", { class: "grow im-val" }, text));
      // filters: the item's conversations in the current scope (all of them if none is)
      const { convs, span } = scopeOf(ref);
      const cs = convs.map((id) => G.convs.get(id)).filter(Boolean);
      const inScope = cs.filter((c) => convVisible(c.id));
      const filters = [];
      for (const [key, ic, name] of FILTER_ROWS) {
        const vals = new Map();
        for (const c of inScope.length ? inScope : cs) {
          const v = key === "conv" ? c.id : c[key];
          if (!v || vals.has(v)) continue;
          vals.set(v, key === "conv" ? [c.title, { host: c.host, user: c.user, harness: c.harness, conv: c.id }] : [v, { [key]: v }]);
        }
        for (const [v, [text, patch]] of [...vals].slice(0, 3)) filters.push(filterItem(ic, name, text, patch, G.filter[key] === v));
        if (vals.size > 3) filters.push(el("div", { class: "im-note" }, `+ ${vals.size - 3} more ${name.toLowerCase()}s`));
      }
      const term = G.nodes.get(ref.replace(/^term:/, ""));
      if (term && term.type === "entity") {
        const sessions = termSessions(term);
        filters.unshift(el("button", {
          class: "im-filter", role: "menuitem", title: `Show only the sessions that mention “${term.label}”, on any host, user or agent`,
          onclick: () => { menu.remove(); onSessions(sessions, `containing “${term.label}”`); },
        }, icon("travel_explore"), el("span", { class: "grow" }, "Show all sessions containing it"), el("span", { class: "sub" }, fmt(sessions.length))));
      }
      const [lo, hi] = span;
      if (lo) {
        const patch = { from: dayStart(lo), to: dayStart(hi || lo, 1) };
        const text = shortDay(lo) === shortDay(hi || lo) ? shortDay(lo) : `${shortDay(lo)} – ${shortDay(hi)}`;
        filters.push(filterItem("schedule", "Time", text, patch, G.filter.from === patch.from && G.filter.to === patch.to));
      }
      const render = () => {
        const parts = [el("div", { class: "tm-head" }, what, el("b", { title: label }, label))];
        if (filters.length) parts.push(el("div", { class: "tm-sub" }, "Filter on"), ...filters);
        if (!target) {
          parts.push(el("div", { class: "im-note" }, "Tool hubs can't be tagged – tag the individual calls instead."));
          return menu.replaceChildren(...parts);
        }
        const cur = new Set(tagsFor(target));
        const a = annOf(target);
        const ta = el("textarea", { class: "text-input", placeholder: "Analyst comment…" });
        ta.value = a ? a.comment : "";
        menu.replaceChildren(...parts, el("div", { class: "tm-sub" }, "Tags"),
          ...G.tags.map((t) => el("button", {
            role: "menuitemcheckbox", "aria-checked": cur.has(t.name) ? "true" : "false",
            onclick: async () => { await toggleTag(target, t.name); render(); },
          }, icon(cur.has(t.name) ? "check_box" : "check_box_outline_blank"), el("span", { class: "dot", style: { background: t.color } }),
            el("span", { class: "grow" }, t.name))),
          el("button", { onclick: async () => { const t = await promptTag(); if (t) { await toggleTag(target, t); render(); } } }, icon("add"), el("span", { class: "grow" }, "New tag…")),
          el("div", { class: "tm-comment" }, ta, el("div", { class: "tm-actions" },
            el("button", { class: "btn text sm", onclick: async () => { await saveAnnotation(target, [], ""); menu.remove(); } }, "Clear all"),
            el("button", { class: "btn filled sm", onclick: async () => { await saveAnnotation(target, [...tagsFor(target)], ta.value); menu.remove(); snack("Comment saved"); } }, "Save comment"))));
      };
      render();
      placeMenu(menu, x, y);
    }

    /* findings */
    function endpointLabel(key) {
      const n = G.nodes.get("ent:" + key);
      if (n) return n.label;
      const ev = G.events.get(key);
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
    /** "host / user · conversation", " · turn", " · time" of a finding */
    function findingWhere(f, fallback = "") {
      const c = G.convs.get(f.conv), ev = G.events.get(f.event);
      return [c ? `${c.host} / ${c.user} · ${c.title}` : fallback, ev ? ` · ${ev.label}` : "", ev && ev.ts ? ` · ${fmtTime(ev.ts)}` : ""];
    }
    /** a finding card: severity, title (+ titleExtra), detail, data-flow chain and an optional meta line */
    const findingCard = (f, attrs = {}, titleExtra = null, meta = null) => el("div", { class: `finding sev-${f.severity}`, ...attrs },
      el("span", { class: "sev-chip" }, f.severity),
      el("span", { class: "f-title" }, f.label, titleExtra),
      el("span", { class: "f-detail" }, f.detail),
      f.chain.length ? chainEl(f.chain) : null,
      meta ? el("div", { class: "f-meta" }, meta) : null);

    return {
      kind, kindOf, kindGroup, avatarHTML, convMatchesFilter, convVisible, windowOn, inWindow, eventVisible, paraVisible, nodeInWindow,
      targetOf, annOf, tagsFor, tagInfo, nodeTags, seenRange, labelFor, convFor, tsFor,
      loadAnnotations, putAnnotation, toggleTag, newTag, tagChipsHTML, annotationView, itemMenu,
      endpointLabel, chainEl, findingWhere, findingCard,
    };
  }

  return {
    store, api, esc, el, icon, applyTheme, effectiveTheme, cssVar, snack, debounce, metaChips,
    fmt, plural, secs, fmtTime, fmtDay, fmtRange, isoToLocalInput, localInputToIso, SCOPE_KEYS, emptyScope, narrowScope, scopeFromURL, pageURL, makeRegex, strHash, download, downloadCSV, closeMenus, placeMenu, loading, inspect,
    SEV_ORDER, SEV_COLOR, sevRank, worstSeverity, STRUCTURAL, KIND_GROUPS, LAYERS, ROLE_ICON, kindKey, loadKinds, model,
  };
})();
