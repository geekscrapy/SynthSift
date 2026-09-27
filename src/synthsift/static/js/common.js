/* Shared helpers for every page: API, DOM, theme, snackbar, dialogs, the loading screen and the module inspector. */
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
  function metaChips(meta) {
    const out = [];
    for (const [ic, get, title, cls] of META_CHIPS) {
      const v = meta && get(meta);
      if (v) out.push(el("span", { class: `tag${cls ? " " + cls : ""}`, title: `${title}: ${v}` }, icon(ic, "xs"), String(v)));
    }
    return out;
  }

  const fmt = (n) => Number(n).toLocaleString();
  const plural = (n, word, many) => `${fmt(n)} ${n === 1 ? word : many || word + "s"}`;
  const secs = (s) => (s >= 90 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : s >= 10 ? `${Math.round(s)} s` : `${(s || 0).toFixed(1)} s`);

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
    return { close, card };
  }

  /* ---------------------------------------------------- loading screen */
  // Shows the pipeline while the server works: stages, and every enrichment
  // module with its own progress.  Blocking (full screen) when there is nothing
  // to look at yet, otherwise a card in the corner that can be minimised.
  const loading = (() => {
    let root = null;
    let errorShown = "";
    const STATE_ICON = { done: "check_circle", cached: "check_circle", skipped: "remove", error: "error", waiting: "radio_button_unchecked" };
    const KIND_LABEL = { extraction: "extraction", feature: "feature", label: "label", analysis: "analysis" };
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
        el("span", { class: "grow lc-name" }, m.label, el("span", { class: `kind-chip ${m.kind}` }, KIND_LABEL[m.kind] || m.kind),
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
    return { update, hide };
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

  return { store, api, esc, el, icon, applyTheme, effectiveTheme, cssVar, snack, debounce, fmt, plural, secs, metaChips,
           dialog, loading, inspect };
})();
