/* SynthSift settings page – rendered generically from the server-side schema. */
"use strict";

(() => {
  const { api, el, icon, snack, fmt, plural, store } = SS;
  const $ = (id) => document.getElementById(id);

  const SECTION_ICONS = {
    "Extraction": "science",
    "Text sources": "description",
    "Custom vocabulary": "sell",
    "Graph content": "hub",
    "Edges & relations": "account_tree",
    "Layout & physics": "bubble_chart",
    "Appearance": "palette",
    "Transcript panel": "forum",
    "Colours": "label",
  };
  const SECTION_HELP = {
    "Extraction": "How entities are found. All of it is classic NLP – regular expressions, vocabularies, spaCy and WordNet – no LLM.",
    "Text sources": "Which parts of a transcript are analysed and how deeply.",
    "Custom vocabulary": "Teach SynthSift your own domain terms and patterns.",
    "Graph content": "Which nodes are drawn.",
    "Edges & relations": "How nodes are connected.",
    "Layout & physics": "Positioning of the graph (applies instantly).",
    "Appearance": "Look of nodes, labels and edges.",
    "Transcript panel": "The right-hand transcript and matches panel.",
    "Colours": "Colour of each node type and entity category.",
  };
  const SCOPE_LABEL = { parse: "Re-analyses", graph: "Rebuilds graph", view: "Instant" };

  const state = { schema: [], values: {}, dirty: {}, models: [], harnesses: [] };
  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");

  async function load() {
    const st = await api("/api/settings");
    state.schema = st.schema;
    state.values = st.values;
    state.models = st.installed_models;
    state.harnesses = st.harnesses;
    SS.applyTheme(store.get("theme", st.values.theme));
    render();
    if (location.hash) setTimeout(() => document.querySelector(location.hash)?.scrollIntoView(), 50);
  }

  const current = (key) => (key in state.dirty ? state.dirty[key] : state.values[key]);

  function setValue(f, v) {
    if (JSON.stringify(v) === JSON.stringify(state.values[f.key])) delete state.dirty[f.key];
    else state.dirty[f.key] = v;
    document.querySelector(`[data-row="${CSS.escape(f.key)}"]`)?.classList.toggle("dirty", f.key in state.dirty);
    updateSavebar();
  }

  function updateSavebar() {
    const n = Object.keys(state.dirty).length;
    $("savebar").classList.toggle("hidden", n === 0);
    const scopes = new Set(Object.keys(state.dirty).map((k) => state.schema.find((f) => f.key === k).scope));
    const effect = scopes.has("parse") ? " · transcripts will be re-analysed" : scopes.has("graph") ? " · graph will be rebuilt" : "";
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
        const num = el("input", { class: "text-input num", type: "number", value: v, min: f.min ?? undefined, max: f.max ?? undefined, step: f.step ?? (f.type === "int" ? 1 : 0.01) });
        const wrap = el("div", { class: "ctl" });
        if (f.min !== null && f.max !== null && f.max - f.min <= 5000) {
          const range = el("input", { type: "range", min: f.min, max: f.max, step: f.step ?? (f.type === "int" ? 1 : 0.01), value: v });
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
        const row = el("div", { class: "chip-row" });
        for (const o of f.options) {
          const chip = el("button", { class: `chip sm${set.has(o) ? " selected" : ""}`, type: "button" }, set.has(o) ? icon("check", "xs") : null, o);
          chip.addEventListener("click", () => {
            const cur = new Set(current(f.key));
            cur.has(o) ? cur.delete(o) : cur.add(o);
            setValue(f, f.options.filter((x) => cur.has(x)));
            rerenderRow(f);
          });
          row.append(chip);
        }
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
      case "color": {
        const c = el("input", { type: "color", value: v, oninput: (e) => setValue(f, e.target.value) });
        return c;
      }
      default: {
        const t = el("input", { class: "text-input", value: v, oninput: (e) => setValue(f, e.target.value) });
        return t;
      }
    }
  }

  function rerenderRow(f) {
    const row = document.querySelector(`[data-row="${CSS.escape(f.key)}"]`);
    if (row) row.replaceWith(renderRow(f));
  }

  function renderRow(f) {
    const wide = f.type === "textarea" || f.type === "multiselect";
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
    for (const s of sections) {
      const id = slug(s.name);
      nav.append(el("a", { href: "#" + id }, icon(SECTION_ICONS[s.name] || "tune"), el("span", {}, s.name)));
      main.append(el("h2", { id }, s.name), el("p", {}, SECTION_HELP[s.name] || ""));
      if (s.name === "Colours") {
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

  async function watchProgress() {
    const bar = $("progress");
    for (let i = 0; i < 600; i++) {
      let st;
      try { st = await api("/api/status"); } catch (e) { return; }
      if (st.state !== "running") {
        bar.classList.add("hidden");
        if (st.state === "error") snack("Processing failed: " + st.error, null, 10000);
        else if (i > 0) snack("Done – " + st.message, { label: "Open graph", run: () => (location.href = "/") }, 8000);
        return;
      }
      bar.classList.remove("hidden");
      bar.querySelector(".bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
      await new Promise((r) => setTimeout(r, 500));
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
      if (scopes.has("parse") || scopes.has("graph")) {
        snack(scopes.has("parse") ? "Saved – re-analysing transcripts…" : "Saved – rebuilding graph…", null, 0);
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
    snack("Settings reset – re-analysing…", null, 0);
    watchProgress();
  });
  $("filter").addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    for (const row of document.querySelectorAll("[data-search]")) row.classList.toggle("hidden", !!q && !row.dataset.search.includes(q));
  });
  window.addEventListener("beforeunload", (e) => { if (Object.keys(state.dirty).length) { e.preventDefault(); e.returnValue = ""; } });

  load().catch((e) => snack("Could not load settings: " + e.message, null, 0));
})();
