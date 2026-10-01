/* SynthSift settings page – rendered generically from the server-side schema. */
"use strict";

(() => {
  const { api, el, icon, snack, fmt, plural, store } = SS;
  const $ = (id) => document.getElementById(id);

  const SECTION_ICONS = {
    "Modules": "extension",
    "Text sources": "description",
    "Processing": "memory",
    "Graph content": "hub",
    "Edges & relations": "account_tree",
    "Layout & physics": "bubble_chart",
    "Appearance": "palette",
    "Transcript panel": "forum",
    "Colours": "label",
  };
  const SECTION_HELP = {
    "Modules": "Enrichment steps run over every paragraph and store their results in the database, each in its own tables. " +
      "Switch modules on or off and tune them here; only what a change affects is recomputed. All of it is classic NLP and matching – no LLM.",
    "Text sources": "Which parts of a transcript are analysed and how deeply.",
    "Processing": "How enrichment runs. Modules that don't depend on each other run side by side, and large batches of new paragraphs are spread over worker processes.",
    "Graph content": "Which nodes are drawn.",
    "Edges & relations": "How nodes are connected.",
    "Layout & physics": "Positioning of the graph (applies instantly).",
    "Appearance": "Look of nodes, labels and edges.",
    "Transcript panel": "The right-hand transcript and matches panel.",
    "Colours": "Colour of each node type and entity category.",
  };
  const SCOPE_LABEL = { segment: "Re-splits & re-analyses", parse: "Re-runs module", graph: "Rebuilds graph", view: "Instant", system: "Next run" };
  const KIND_GROUP = {
    extraction: "Extraction · entity candidates for the graph",
    feature: "Features · numbers per paragraph",
    label: "Labels · a class per paragraph",
    analysis: "Analysis · results over the whole corpus",
  };
  const SECTION_ORDER = ["Modules", "Text sources", "Processing"];

  const state = { schema: [], values: {}, dirty: {}, models: [], harnesses: [], modules: [], stats: new Map() };
  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");

  async function load() {
    const st = await api("/api/settings");
    state.schema = st.schema;
    state.values = st.values;
    state.models = st.installed_models;
    state.harnesses = st.harnesses;
    state.modules = st.modules || [];
    await loadStats();
    SS.applyTheme(store.get("theme", st.values.theme));
    render();
    if (location.hash) setTimeout(() => document.querySelector(location.hash)?.scrollIntoView(), 50);
  }

  const current = (key) => (key in state.dirty ? state.dirty[key] : state.values[key]);
  const moduleDirty = (name) => Object.keys(state.dirty).some((k) => (state.schema.find((x) => x.key === k) || {}).module === name);

  async function loadStats() {
    try {
      const m = await api("/api/modules");
      state.stats = new Map(m.modules.map((x) => [x.name, x]));
    } catch (e) { /* server busy – cards render without stats */ }
  }

  function setValue(f, v) {
    if (JSON.stringify(v) === JSON.stringify(state.values[f.key])) delete state.dirty[f.key];
    else state.dirty[f.key] = v;
    document.querySelector(`[data-row="${CSS.escape(f.key)}"]`)?.classList.toggle("dirty", f.key in state.dirty);
    if (f.module) document.querySelector(`[data-module="${CSS.escape(f.module)}"]`)?.classList.toggle("dirty", moduleDirty(f.module));
    updateSavebar();
  }

  function updateSavebar() {
    const n = Object.keys(state.dirty).length;
    $("savebar").classList.toggle("hidden", n === 0);
    const scopes = new Set(Object.keys(state.dirty).map((k) => state.schema.find((f) => f.key === k).scope));
    const effect = scopes.has("segment") ? " · transcripts will be re-split and re-analysed"
      : scopes.has("parse") ? " · affected modules will re-run" : scopes.has("graph") ? " · graph will be rebuilt" : "";
    $("dirty-text").textContent = `${plural(n, "unsaved change")}${effect}`;
  }

  function control(f) {
    const v = current(f.key);
    switch (f.type) {
      case "bool": {
        const input = el("input", { type: "checkbox", checked: !!v, onchange: (e) => setValue(f, e.target.checked) });
        return el("label", { class: "switch" }, input, el("span", { class: "track" }));
      }
      case "int":
      case "float": {
        const step = f.step ?? (f.type === "int" ? 1 : 0.01);
        const num = el("input", { class: "text-input num", type: "number", value: v, min: f.min ?? undefined, max: f.max ?? undefined, step });
        const wrap = el("div", { class: "ctl" });
        if (f.min !== null && f.max !== null && f.max - f.min <= 5000) {
          const range = el("input", { type: "range", min: f.min, max: f.max, step, value: v });
          range.addEventListener("input", () => { num.value = range.value; setValue(f, f.type === "int" ? parseInt(range.value, 10) : parseFloat(range.value)); });
          num.addEventListener("input", () => { range.value = num.value; });
          wrap.append(range);
        }
        num.addEventListener("change", () => setValue(f, f.type === "int" ? parseInt(num.value, 10) : parseFloat(num.value)));
        wrap.append(num);
        return wrap;
      }
      case "select": {
        const sel = el("select", { class: "select", onchange: (e) => setValue(f, e.target.value) });
        for (const o of f.options) {
          const missing = f.key === "spacy_model" && !state.models.includes(o);
          sel.append(el("option", { value: o, selected: o === v }, missing ? `${o} (not installed)` : o));
        }
        return sel;
      }
      case "multiselect": {
        const set = new Set(v);
        const row = el("div", { class: "chip-row" }, f.options.map((o) => SS.onlyChip({
          key: o, label: o, set, keys: f.options, mode: "list", attrs: { type: "button" },
          onChange: () => { setValue(f, f.options.filter((x) => set.has(x))); rerenderRow(f); },
          children: [set.has(o) ? icon("check", "xs") : null, o],
        })));
        const all = el("button", { class: "btn text sm", type: "button", onclick: () => { setValue(f, [...f.options]); rerenderRow(f); } }, "All");
        const none = el("button", { class: "btn text sm", type: "button", onclick: () => { setValue(f, []); rerenderRow(f); } }, "None");
        return el("div", {}, row, el("div", { style: { marginTop: "6px" } }, all, none));
      }
      case "textarea": {
        const ta = el("textarea", { class: "text-input", rows: Math.min(14, Math.max(4, String(v).split("\n").length + 1)), spellcheck: "false" });
        ta.value = v;
        ta.addEventListener("input", () => setValue(f, ta.value));
        return ta;
      }
      case "color":
        return el("input", { type: "color", value: v, oninput: (e) => setValue(f, e.target.value) });
      case "lists":
        return listsControl();
      default:
        return el("input", { class: "text-input", value: v, oninput: (e) => setValue(f, e.target.value) });
    }
  }

  function rerenderRow(f) {
    const row = document.querySelector(`[data-row="${CSS.escape(f.key)}"]`);
    if (row) row.replaceWith(renderRow(f));
  }

  function renderRow(f) {
    if (f.type === "hidden") return null;
    const wide = f.type === "textarea" || f.type === "multiselect" || f.type === "lists";
    return el("div", { class: `set-row${wide ? " wide" : ""}${f.key in state.dirty ? " dirty" : ""}`, "data-row": f.key, "data-search": `${f.label} ${f.help} ${f.key}`.toLowerCase() },
      el("div", { class: "lbl" },
        el("div", { class: "name" }, f.label, el("span", { class: `scope ${f.scope}`, title: "What happens when this changes" }, SCOPE_LABEL[f.scope])),
        f.help ? el("div", { class: "help" }, f.help) : null),
      el("div", { class: "ctl" }, control(f)));
  }

  function render() {
    const main = $("main");
    const nav = $("nav");
    main.replaceChildren();
    nav.replaceChildren();
    const sections = [];
    for (const f of state.schema) {
      let s = sections.find((x) => x.name === f.section);
      if (!s) sections.push((s = { name: f.section, fields: [] }));
      s.fields.push(f);
    }
    const rank = (n) => (SECTION_ORDER.includes(n) ? SECTION_ORDER.indexOf(n) : SECTION_ORDER.length);
    sections.sort((a, b) => rank(a.name) - rank(b.name));
    for (const s of sections) {
      const id = slug(s.name);
      nav.append(el("a", { href: "#" + id }, icon(SECTION_ICONS[s.name] || "tune"), el("span", {}, s.name)));
      main.append(el("h2", { id }, s.name), el("p", {}, SECTION_HELP[s.name] || ""));
      if (s.name === "Modules") {
        renderModules(main);
      } else if (s.name === "Colours") {
        const grid = el("div", { class: "color-grid" });
        for (const f of s.fields) {
          const row = el("label", { "data-row": f.key, "data-search": `${f.label} colour ${f.key}`.toLowerCase() }, control(f), f.label);
          grid.append(row);
        }
        main.append(el("div", { class: "set-card" }, grid));
      } else {
        main.append(el("div", { class: "set-card" }, s.fields.map(renderRow)));
      }
    }
    // data & harnesses
    nav.append(el("a", { href: "#data" }, icon("dataset"), el("span", {}, "Data")), el("a", { href: "#harnesses" }, icon("terminal"), el("span", {}, "Harnesses")));
    main.append(el("h2", { id: "data" }, "Data"), el("p", {}, "Uploaded transcript archives. Deleting one removes its conversations from the graph."));
    const dataCard = el("div", { class: "set-card", id: "datasets" });
    main.append(dataCard);
    renderDatasets(dataCard);
    main.append(el("h2", { id: "harnesses" }, "Harnesses"),
      el("p", {}, "Transcript parsers. The harness folder name in the zip picks the parser; add one by dropping a module into synthsift/harnesses/."));
    const table = el("table", { class: "data-table" },
      el("thead", {}, el("tr", {}, el("th", {}, "Harness"), el("th", {}, "Folder names"), el("th", {}, "Status"), el("th", {}, "Notes"))),
      el("tbody", {}, state.harnesses.map((h) => el("tr", {},
        el("td", {}, el("b", {}, h.label), el("div", { class: "muted mono" }, h.name)),
        el("td", { class: "mono" }, [h.name, ...h.aliases].join(", ")),
        el("td", {}, h.implemented ? el("span", { class: "status-ok" }, icon("check", "xs"), " Ready") : el("span", { class: "status-stub" }, icon("build", "xs"), " Stub")),
        el("td", { class: "muted" }, h.description)))));
    main.append(el("div", { class: "set-card" }, table,
      el("pre", { class: "layout-hint" }, "upload.zip\n└── <host>/\n    └── <user>/\n        └── <harness>/          e.g. example, claude_code, gemini, antigravity, hermes, openclaw\n            ├── transcript1-xyz.json\n            └── transcript2-abc.jsonl")));
    setupScrollSpy();
  }

  /* ------------------------------------------------------------ modules */
  function renderModules(main) {
    let kind = "";
    for (const m of state.modules) {
      if (m.helper_of) continue; // its options live in the card of the module it serves
      if (m.kind !== kind) { kind = m.kind; main.append(el("div", { class: "mod-group" }, KIND_GROUP[kind] || kind)); }
      main.append(moduleCard(m));
    }
  }

  const modLabel = (name) => (state.modules.find((m) => m.name === name) || { label: name }).label;

  function moduleCard(m) {
    const field = m.switch ? state.schema.find((f) => f.key === m.switch) : null;
    const on = m.core || !field || !!current(m.switch);
    const opts = state.schema.filter((f) => f.section === "Modules" && f.module === m.name && f.key !== m.switch && f.type !== "hidden");
    const helpers = state.modules.filter((x) => x.helper_of === m.name);
    const stats = [m, ...helpers].map((x) => state.stats.get(x.name)).filter(Boolean);
    const dirty = moduleDirty(m.name);
    const openKey = `modOpen.${m.name}`;
    const open = store.get(openKey, on && opts.length > 0 && opts.length <= 6) || dirty;

    const sw = field ? el("label", { class: "switch", title: on ? "Module on" : "Module off" },
      el("input", { type: "checkbox", checked: on, onchange: (e) => { setValue(field, e.target.checked); rerenderCard(m); } }),
      el("span", { class: "track" })) : el("span", { class: "mod-core", title: "Always runs: the graph is built from its output" }, "Always on");

    const meta = [];
    const deps = (stats[0] && stats[0].requires) || m.requires;
    if (deps && deps.length) meta.push(el("span", { class: "tag", title: "Runs after these modules and reads their output" }, icon("call_merge", "xs"), "after " + deps.map(modLabel).join(", ")));
    meta.push(el("span", { class: "tag", title: m.scope === "corpus" ? "Runs once over the whole corpus" : "Runs per paragraph; only new paragraphs are processed" },
      icon(m.scope === "corpus" ? "dataset" : "segment", "xs"), m.scope === "corpus" ? "whole corpus" : m.parallel ? "per paragraph · parallel" : "per paragraph"));
    for (const st of stats) {
      if (st.processed !== undefined && on) meta.push(el("span", { class: "tag", title: "Paragraphs this module has processed" }, icon("done_all", "xs"), `${fmt(st.processed)} / ${fmt(st.paragraphs)} paragraphs`));
      for (const t of st.tables) {
        meta.push(el("span", { class: "tag mono", title: t.description || t.name }, icon("table", "xs"), `${t.name} · ${fmt(t.rows)}`,
          t.rows ? el("a", { href: `/api/modules/${encodeURIComponent(st.name)}/export?table=${encodeURIComponent(t.name)}`, title: "Download as CSV (one row per paragraph occurrence)" }, icon("download", "xs")) : null));
      }
      const run = st.last_run;
      if (run && on) meta.push(el("span", { class: "tag", title: "Last run" }, icon(run.state === "cached" ? "history" : "timer", "xs"),
        run.state === "cached" ? "up to date" : run.state === "done" ? `ran in ${SS.secs(run.seconds)}` : run.state));
    }

    const body = el("div", { class: "mod-body" + (open ? "" : " hidden") }, opts.map(renderRow));
    const toggle = opts.length ? el("button", { class: "btn text sm mod-toggle-opts", type: "button", onclick: () => {
      const now = body.classList.toggle("hidden");
      store.set(openKey, !now);
      toggle.replaceChildren(icon(now ? "expand_more" : "expand_less", "sm"), `${now ? "Options" : "Hide"} (${opts.length})`);
    } }, icon(open ? "expand_less" : "expand_more", "sm"), `${open ? "Hide" : "Options"} (${opts.length})`) : null;

    const search = [m.label, m.description, m.name, ...opts.map((f) => `${f.label} ${f.help} ${f.key}`)].join(" ").toLowerCase();
    return el("div", { class: `mod-card${on ? "" : " off"}${dirty ? " dirty" : ""}`, "data-module": m.name, "data-search": search },
      el("div", { class: "mod-head" }, sw,
        el("div", { class: "grow" },
          el("div", { class: "mod-title" }, m.label, el("span", { class: `kind-chip ${m.kind}` }, m.kind),
            field ? el("span", { class: `scope ${field.scope}`, title: "What happens when this changes" }, SCOPE_LABEL[field.scope]) : null),
          el("div", { class: "mod-desc" }, m.description),
          el("div", { class: "mod-meta" }, meta)),
        toggle),
      body);
  }

  function rerenderCard(m) {
    const card = document.querySelector(`[data-module="${CSS.escape(m.name)}"]`);
    if (card) card.replaceWith(moduleCard(m));
  }

  /* IOC / keyword lists: uploaded files live on the server; changes apply at once (no Save) */
  function listsControl() {
    const box = el("div", { class: "lists-box" }, el("div", { class: "muted" }, "Loading lists…"));
    api("/api/lists").then((r) => drawLists(box, r.lists)).catch((e) => box.replaceChildren(el("div", { class: "muted" }, e.message)));
    return box;
  }

  function drawLists(box, lists) {
    const on = !!current("mod.ioc");
    const input = el("input", { type: "file", multiple: true, accept: ".txt,.csv,.tsv,.list,.ioc,.lst,.gz", class: "hidden",
      onchange: (e) => upload([...e.target.files]) });
    const progress = el("div", { class: "upload-row hidden" });
    const zone = el("div", { class: "drop-zone" }, icon("upload_file"),
      el("span", { class: "grow" }, "Drop lists here – plain text (one value per line) or CSV / TSV with a value column plus optional type, label and severity. Millions of lines are fine; .gz works too."),
      el("button", { class: "btn tonal sm", type: "button", onclick: () => input.click() }, icon("add", "sm"), "Upload lists"), input);
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); upload([...e.dataTransfer.files]); });

    function upload(files) {
      if (!files.length) return;
      const fd = new FormData();
      for (const f of files) fd.append("files", f);
      const bar = el("div", { class: "bar", style: { width: "0%" } });
      const label = el("span", {}, `Uploading ${plural(files.length, "file")}…`);
      progress.replaceChildren(el("span", { class: "spinner sm" }), label, el("div", { class: "mini-bar" }, bar));
      progress.classList.remove("hidden");
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/lists");
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) { bar.style.width = `${Math.round((100 * e.loaded) / e.total)}%`; label.textContent = `Uploading ${fmt(Math.round(e.loaded / 1e6))} / ${fmt(Math.round(e.total / 1e6))} MB…`; } };
      xhr.onload = () => {
        progress.classList.add("hidden");
        let res = {};
        try { res = JSON.parse(xhr.responseText); } catch (e) { /* not json */ }
        if (xhr.status >= 400) { snack("Upload failed: " + (res.detail || xhr.statusText), null, 8000); return; }
        drawLists(box, res.lists);
        if (on) { snack("Lists uploaded – matching…", null, 3000); watchProgress(); }
        else snack("Lists uploaded. Turn on the module and save to match them.", null, 6000);
      };
      xhr.onerror = () => { progress.classList.add("hidden"); snack("Upload failed", null, 6000); };
      xhr.send(fd);
    }

    const rows = lists.map((l) => el("tr", {},
      el("td", {}, el("b", { class: "mono" }, l.name)),
      el("td", { class: "num" }, l.entries === null || l.entries === undefined ? el("span", { class: "muted", title: "Counted when the module next runs" }, "–") : `${fmt(l.entries)} entries`),
      el("td", { class: "num muted" }, l.bytes >= 1e6 ? `${(l.bytes / 1e6).toFixed(1)} MB` : `${fmt(Math.ceil(l.bytes / 1e3))} kB`),
      el("td", {}, el("label", { class: "switch", title: l.enabled ? "Used for matching" : "Ignored" },
        el("input", { type: "checkbox", checked: l.enabled, onchange: async (e) => {
          const r2 = await api(`/api/lists/${encodeURIComponent(l.name)}`, { method: "PUT", body: { enabled: e.target.checked } });
          drawLists(box, r2.lists);
          if (on) watchProgress();
        } }), el("span", { class: "track" }))),
      el("td", { style: { textAlign: "right" } }, el("button", { class: "icon-btn sm", title: "Delete list", onclick: async () => {
        if (!confirm(`Delete ${l.name}?`)) return;
        const r2 = await api(`/api/lists/${encodeURIComponent(l.name)}`, { method: "DELETE" });
        drawLists(box, r2.lists);
        if (on) watchProgress();
      } }, icon("delete", "sm")))));
    box.replaceChildren(...[
      lists.length ? el("table", { class: "data-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "List"), el("th", {}, "Entries"), el("th", {}, "Size"), el("th", {}, "Use"), el("th", {}))),
        el("tbody", {}, rows)) : null,
      zone, progress,
      !on && lists.length ? el("div", { class: "muted" }, icon("info", "xs"), " The module is off – switch it on above and save to match these lists.") : null].filter(Boolean));
  }

  async function renderDatasets(card) {
    let list = [];
    try { list = await api("/api/datasets"); } catch (e) { /* ignore */ }
    const rows = list.map((d) => el("tr", {},
      el("td", {}, el("b", {}, d.name), el("div", { class: "muted mono" }, d.id)),
      el("td", {}, `${fmt(d.files)} files · ${plural(d.conversations, "conversation")}`),
      el("td", {}, new Date(d.uploaded_at * 1000).toLocaleString()),
      el("td", {}, (d.warnings || []).length ? el("span", { class: "status-stub", title: d.warnings.join("\n") }, icon("warning", "xs"), ` ${d.warnings.length}`) : ""),
      el("td", { style: { textAlign: "right" } }, el("button", { class: "icon-btn sm", title: "Delete", onclick: async () => {
        if (!confirm(`Delete ${d.name}?`)) return;
        await api(`/api/datasets/${encodeURIComponent(d.id)}`, { method: "DELETE" });
        snack(`Deleted ${d.name}`);
        renderDatasets(card);
      } }, icon("delete", "sm")))));
    card.replaceChildren(
      list.length
        ? el("table", { class: "data-table" }, el("thead", {}, el("tr", {}, el("th", {}, "Archive"), el("th", {}, "Contents"), el("th", {}, "Uploaded"), el("th", {}, "Warnings"), el("th", {}))), el("tbody", {}, rows))
        : el("div", { class: "set-row" }, el("div", { class: "lbl muted" }, "No archives uploaded yet.")),
      el("div", { class: "set-row" }, el("div", { class: "lbl" }, el("div", { class: "name" }, "Remove everything"), el("div", { class: "help" }, "Deletes all uploaded archives from the data directory.")),
        el("div", { class: "ctl" }, el("button", { class: "btn outlined", disabled: !list.length, onclick: async () => {
          if (!confirm("Delete all uploaded archives?")) return;
          await api("/api/datasets", { method: "DELETE" });
          snack("All archives deleted");
          renderDatasets(card);
        } }, icon("delete"), "Delete all"))));
  }

  function setupScrollSpy() {
    const links = [...$("nav").querySelectorAll("a")];
    const main = $("main");
    const onScroll = () => {
      let cur = links[0];
      for (const a of links) {
        const h = document.querySelector(a.getAttribute("href"));
        if (h && h.offsetTop - main.scrollTop < 120) cur = a;
      }
      links.forEach((a) => a.classList.toggle("on", a === cur));
    };
    main.addEventListener("scroll", onScroll);
    onScroll();
  }

  let watching = false;
  async function watchProgress() {
    if (watching) return;
    watching = true;
    const bar = $("progress");
    try {
      await new Promise((r) => setTimeout(r, 250));
      for (let i = 0; i < 7200; i++) {
        let st;
        try { st = await api("/api/status"); } catch (e) { return; }
        SS.loading.update(st);
        if (st.state !== "running") {
          bar.classList.add("hidden");
          if (st.state !== "error" && i > 0) snack("Done – " + st.message, { label: "Open graph", run: () => (location.href = "/") }, 8000);
          await loadStats();
          for (const m of state.modules) if (!m.helper_of) rerenderCard(m);
          return;
        }
        bar.classList.remove("hidden");
        bar.querySelector(".bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
        await new Promise((r) => setTimeout(r, 500));
      }
    } finally {
      watching = false;
    }
  }

  $("save").addEventListener("click", async () => {
    try {
      const res = await api("/api/settings", { method: "PUT", body: state.dirty });
      state.values = res.values;
      if ("theme" in state.dirty) SS.applyTheme(state.values.theme);
      state.dirty = {};
      render();
      updateSavebar();
      const scopes = new Set(res.scopes);
      if (scopes.has("segment") || scopes.has("parse") || scopes.has("graph")) {
        snack(scopes.has("segment") ? "Saved – re-analysing transcripts…" : scopes.has("parse") ? "Saved – re-running modules…" : "Saved – rebuilding graph…", null, 3000);
        watchProgress();
      } else snack("Saved", { label: "Open graph", run: () => (location.href = "/") });
    } catch (e) {
      snack("Save failed: " + e.message);
    }
  });
  $("discard").addEventListener("click", () => { state.dirty = {}; render(); updateSavebar(); });
  $("reset").addEventListener("click", async () => {
    if (!confirm("Reset every setting to its default? Transcripts will be re-analysed.")) return;
    const res = await api("/api/settings/reset", { method: "POST" });
    state.values = res.values;
    state.dirty = {};
    SS.applyTheme(state.values.theme);
    try { for (const k of ["ctxBefore", "ctxAfter", "labels", "underline", "rightWidth"]) localStorage.removeItem("synthsift." + k); } catch (e) { /* ignore */ }
    render();
    updateSavebar();
    snack("Settings reset – re-analysing…", null, 3000);
    watchProgress();
  });
  $("filter").addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    for (const row of document.querySelectorAll("[data-search]")) row.classList.toggle("hidden", !!q && !row.dataset.search.includes(q));
  });
  window.addEventListener("beforeunload", (e) => { if (Object.keys(state.dirty).length) { e.preventDefault(); e.returnValue = ""; } });

  load().then(() => watchProgress()).catch((e) => snack("Could not load settings: " + e.message, null, 0));
})();
