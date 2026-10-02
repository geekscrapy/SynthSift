/* Panels shared by every view: the graph's drawer and right-hand panel, and the filter rail and detail pane of the
 * Nodes and Timeline pages are built from these pieces, so a section, a filter or a detail header looks and behaves
 * the same everywhere.
 *
 *   const P = SS.panels(G, M, { setFilter, onTagFilter, afterTagsChanged, newTag });
 *
 * G is the page's state (data, convs, convOrder, filter, tags, tagFilter, hideIgnored, kinds …) and M its SS.model.
 * Hooks: setFilter(patch) narrows the conversation scope; onTagFilter() runs after the tag filter or "Hide ignored"
 * changed (both are saved here); afterTagsChanged() after a tag was deleted; newTag() creates a tag (promise of its
 * name).
 */
"use strict";

SS.panels = (G, M, hooks = {}) => {
  const { store, api, el, icon, fmt, plural, fmtTime, snack } = SS;

  /* ------------------------------------------------------------ frame */
  /** a panel section: a title row (icon, title, optional count badge, actions on the right) and a body. An action is
   *  { label } (a text button), { icon } (an icon button) or { href } (a link); all take title, run and id. */
  function section({ id = null, cls = "", icon: ic = null, title, count = null, countId = null, actions = [], body = [] }) {
    const act = (a) => {
      const attrs = { id: a.id || null, title: a.title || a.label || null, onclick: a.run || null, type: a.href ? null : "button" };
      if (a.href) return el("a", { ...attrs, class: "pane-act", href: a.href, target: a.target || null }, a.label);
      return a.label ? el("button", { ...attrs, class: "pane-act" }, a.label) : el("button", { ...attrs, class: "icon-btn sm" }, icon(a.icon));
    };
    return el("section", { class: `pane-section ${cls}`.trim(), id },
      el("div", { class: "pane-title" }, ic ? icon(ic, "xs") : null, el("span", { class: "pane-name" }, title),
        count !== null || countId ? el("span", { class: "badge soft", id: countId }, count === null ? "" : fmt(count)) : null,
        el("span", { class: "grow" }), ...actions.filter(Boolean).map(act)),
      ...[].concat(body).filter(Boolean));
  }

  /* ------------------------------------------------------------ scope */
  /** host / user / agent selects, the conversation (a select, or a clearable chip where a tree picks conversations)
   *  and the time window (collapsed to one line until it is set or opened) */
  function scope(container, { conv = "select", onClearConv = null } = {}) {
    const all = G.convOrder.map((c) => G.convs.get(c)).filter(Boolean);
    const f = G.filter;
    const scopes = {
      host: all,
      user: all.filter((c) => !f.host || c.host === f.host),
      harness: all.filter((c) => (!f.host || c.host === f.host) && (!f.user || c.user === f.user)),
    };
    const selects = [];
    for (const [key, ic, label] of [["host", "computer", "All hosts"], ["user", "person", "All users"], ["harness", "terminal", "All agents"]]) {
      const vals = [...new Set(scopes[key].map((c) => c[key]))].sort();
      if (f[key] && !vals.includes(f[key])) f[key] = "";
      const sel = el("select", { "aria-label": label, id: `f-${key}`, "data-scope": key, onchange: (e) => hooks.setFilter({ [key]: e.target.value }) },
        el("option", { value: "" }, `${label} (${vals.length})`), ...vals.map((v) => el("option", { value: v, selected: f[key] === v }, v)));
      selects.push(el("label", { class: `mini-select${f[key] ? " active" : ""}`, title: label }, icon(ic, "xs"), sel));
    }
    if (f.conv && !G.convs.has(f.conv)) f.conv = "";
    let convEl = null;
    if (conv === "select") {
      convEl = el("label", { class: `mini-select wide${f.conv ? " active" : ""}`, title: "Conversation" }, icon("forum", "xs"),
        el("select", { "aria-label": "Conversation", id: "f-conv", "data-scope": "conv", onchange: (e) => hooks.setFilter({ conv: e.target.value }) },
          el("option", { value: "" }, "All conversations"),
          ...all.filter((c) => (!f.host || c.host === f.host) && (!f.user || c.user === f.user) && (!f.harness || c.harness === f.harness))
            .map((c) => el("option", { value: c.id, selected: f.conv === c.id }, c.title))));
    } else if (f.conv) {
      convEl = el("button", { class: "chip sm conv-focus", id: "f-conv", title: "Showing one conversation – click to clear",
        onclick: () => (onClearConv ? onClearConv() : hooks.setFilter({ conv: "" })) },
      icon("forum", "xs"), el("span", { class: "label" }, G.convs.get(f.conv).title), icon("close", "xs"));
    }
    container.replaceChildren(...[el("div", { class: "filter-row" }, ...selects), convEl, timeWindow()].filter(Boolean));
  }

  let timeOpen = false;
  /** the shared time window: "Any time" until set; the from / to inputs show when it is set or opened */
  function timeWindow() {
    const f = G.filter;
    const on = !!(f.from || f.to);
    const inp = (key, label) => el("input", {
      type: "datetime-local", class: "text-input", "aria-label": label, title: label, value: SS.isoToLocalInput(f[key]),
      onchange: (e) => hooks.setFilter({ [key]: SS.localInputToIso(e.target.value) }),
    });
    const box = el("div", { class: `time-row${on ? " active" : ""}`, id: "f-time" });
    const draw = () => {
      const open = on || timeOpen;
      box.replaceChildren(...[
        el("div", { class: "time-head" }, icon("schedule", "xs"), el("span", { class: "grow" }, on ? SS.fmtRange(f.from, f.to) : "Any time"),
          on ? el("button", { class: "icon-btn sm", title: "Clear the time window", onclick: () => hooks.setFilter({ from: "", to: "" }) }, icon("close", "sm"))
            : el("button", { class: "icon-btn sm", title: open ? "Hide the time inputs" : "Limit to a time window", "aria-expanded": open ? "true" : "false",
              onclick: () => { timeOpen = !timeOpen; draw(); } }, icon(open ? "expand_less" : "edit_calendar", "sm"))),
        open ? el("label", {}, el("span", {}, "From"), inp("from", "From (inclusive)")) : null,
        open ? el("label", {}, el("span", {}, "To"), inp("to", "To (exclusive)")) : null].filter(Boolean));
    };
    draw();
    return box;
  }

  /* ------------------------------------------------------------- tags */
  const BUILTIN_TAGS = new Set(["bad", "suspicious", "seen", "ignore"]);
  /** items carrying each tag */
  function tagCounts() {
    const m = new Map();
    for (const a of Object.values(G.annotations)) for (const t of a.tags) m.set(t, (m.get(t) || 0) + 1);
    return m;
  }
  /** tag filter chips (with "only"; right-click a custom tag to delete it) and "Hide ignored" */
  function tagFilter(container, { counts = null, title = "Show only items tagged" } = {}) {
    const c = counts || tagCounts();
    const changed = () => { store.set("tagFilter", [...G.tagFilter]); hooks.onTagFilter && hooks.onTagFilter(); };
    container.replaceChildren(...G.tags.map((t) => SS.onlyChip({
      key: t.name, label: `“${t.name}”`, set: G.tagFilter, cls: `tagf${c.get(t.name) ? "" : " muted-chip"}`, style: { "--tag": t.color },
      title: `${title} “${t.name}”${BUILTIN_TAGS.has(t.name) ? "" : " · right-click to delete the tag"}`, onChange: changed,
      attrs: { oncontextmenu: (e) => { e.preventDefault(); deleteTag(t.name); } },
      children: [el("span", { class: "swatch" }), el("span", { class: "label" }, t.name), el("span", { class: "count" }, fmt(c.get(t.name) || 0))],
    })),
    el("button", {
      class: `chip sm${G.hideIgnored ? " selected" : ""}`, title: "Hide everything tagged “ignore”", "aria-pressed": G.hideIgnored ? "true" : "false",
      onclick: () => { G.hideIgnored = !G.hideIgnored; store.set("hideIgnored", G.hideIgnored); hooks.onTagFilter && hooks.onTagFilter(); },
    }, icon(G.hideIgnored ? "visibility_off" : "visibility", "xs"), "Hide ignored"));
  }
  async function deleteTag(name) {
    if (BUILTIN_TAGS.has(name)) return;
    const yes = await SS.confirmDialog({ title: `Delete the tag “${name}”?`, text: "It is removed from every session, turn and term that carries it.",
      ok: "Delete", danger: true });
    if (!yes) return;
    await api(`/api/tags?name=${encodeURIComponent(name)}`, { method: "DELETE" });
    G.tagFilter.delete(name);
    store.set("tagFilter", [...G.tagFilter]);
    await M.loadAnnotations();
    hooks.afterTagsChanged && hooks.afterTagsChanged();
  }
  /** the actions of a tag section's title: new tag, clear the tag filter */
  const tagActions = ({ onNew = null } = {}) => [
    { icon: "add", title: "New tag", id: "tag-new", run: async () => { const n = await (hooks.newTag || M.newTag)(); if (n) { snack(`Tag “${n}” created`); onNew && onNew(n); } } },
    { icon: "filter_list_off", title: "Clear the tag filter", id: "tag-clear", run: () => { G.tagFilter.clear(); store.set("tagFilter", []); hooks.onTagFilter && hooks.onTagFilter(); } },
  ];
  /** one tri-state chip per tag over some targets (click to tag them all, again to untag), and "New" */
  function tagCoverage(targets) {
    const ts = typeof targets === "function" ? targets() : targets;
    return [...G.tags.map((t) => {
      const s = M.coverage(ts, t.name);
      return el("button", {
        class: `chip sm${s.all ? " all" : ""}`, style: { "--tag": t.color }, role: "checkbox", "aria-checked": s.aria,
        title: s.all ? `Remove “${t.name}” from ${plural(ts.length, "item")}` : `Tag ${plural(ts.length, "item")} “${t.name}”`,
        onclick: () => M.bulkTag(ts, t.name, !s.all),
      }, icon(s.icon, "xs"), t.name);
    }),
    el("button", { class: "chip sm", title: "New tag for all of them", onclick: async () => {
      const n = await (hooks.newTag || M.newTag)();
      if (n) M.bulkTag(typeof targets === "function" ? targets() : targets, n, true);
    } }, icon("add", "xs"), "New")];
  }

  /* ------------------------------------------------------- node types */
  /** node-type chips by group, each with "only", and hide all / show all per group. `counts`: kind key → visible
   *  nodes; hidden kinds stay listed (count 0) so they can be switched back on. onChange(isolated) after a change. */
  function kindLegend(container, { counts, hidden, onChange }) {
    const groups = new Map();
    const add = (key, n) => {
      const g = M.kindGroup(key);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push({ ...M.kind(key), key, n });
    };
    for (const [key, n] of counts) add(key, n);
    for (const key of hidden) if (!counts.has(key) && G.kinds.has(key)) add(key, 0);
    const keys = [...new Set([...counts.keys(), ...hidden])];
    container.replaceChildren(...[...groups.keys()].sort((a, b) => SS.KIND_GROUPS.indexOf(a) - SS.KIND_GROUPS.indexOf(b)).map((g) => {
      const items = groups.get(g).sort((a, b) => b.n - a.n);
      const allOn = items.every((i) => !hidden.has(i.key));
      return el("div", { class: "kind-group" },
        el("h4", {}, g, el("button", { type: "button", onclick: () => { for (const i of items) allOn ? hidden.add(i.key) : hidden.delete(i.key); onChange(false); } },
          allOn ? "hide all" : "show all")),
        el("div", { class: "chip-row" }, items.map((i) => SS.kindChip(i, hidden, keys, onChange))));
    }));
  }
  /** the four layers (dialogue, thoughts, actions, entities) as chips with "only"; counts: layer → visible nodes */
  function layerChips(container, { counts, hidden, onChange, small = true }) {
    container.replaceChildren(...SS.LAYERS.map((l) => SS.onlyChip({
      key: l.key, label: `the ${l.label.toLowerCase()} layer`, set: hidden, keys: SS.LAYERS.map((x) => x.key), mode: "hide",
      onCls: " selected", offCls: small ? " off" : "", small, title: `Show / hide the ${l.label.toLowerCase()} layer`, onChange,
      children: [icon(hidden.has(l.key) ? "visibility_off" : l.icon, small ? "xs" : "sm"), l.label, el("span", { class: "count" }, fmt(counts.get(l.key) || 0))],
    })));
  }

  /* --------------------------------------------------------- security */
  const categories = () => (G.data && G.data.security && G.data.security.categories) || {};
  /** finding counts per severity / category */
  function findingCounts(fs) {
    const sev = new Map(), cat = new Map();
    for (const f of fs) {
      sev.set(f.severity, (sev.get(f.severity) || 0) + 1);
      cat.set(f.category, (cat.get(f.category) || 0) + 1);
    }
    return { sev, cat };
  }
  /** severity chips, worst first; with onPick(sev) they choose a minimum severity (`min` is selected) */
  function sevChips(counts, { min = null, onPick = null } = {}) {
    return el("div", { class: "sec-summary" }, [...SS.SEV_ORDER].reverse().filter((s) => counts.get(s)).map((s) => el(onPick ? "button" : "span", {
      class: `chip sm sev-${s}${min === s ? " selected" : ""}`, title: onPick ? `Show ${s} and above` : `${fmt(counts.get(s))} ${s}`,
      onclick: onPick ? () => onPick(s) : null,
    }, el("span", { class: "sev-chip" }, s), el("span", { class: "count" }, fmt(counts.get(s))))));
  }
  /** finding-category chips with "only"; `selected` is the page's set of category keys */
  function catChips(container, { counts, selected, onChange }) {
    container.replaceChildren(...Object.entries(categories()).filter(([k]) => counts.get(k) || selected.has(k)).map(([key, label]) => SS.onlyChip({
      key, label: label.toLowerCase(), set: selected, title: `Only ${label.toLowerCase()} findings`, onChange: () => onChange(),
      children: [el("span", { class: "label" }, label), el("span", { class: "count" }, fmt(counts.get(key) || 0))],
    })));
  }
  /** "Findings" heading and cards. onOpen(f) makes a card clickable; withConv adds where it happened */
  function findingsSection(fs, { limit = 20, withConv = true, onOpen = null, title = "Findings" } = {}) {
    if (!fs.length) return [];
    const card = (f) => M.findingCard(f, onOpen ? { "data-finding": f.i, title: "Open the turn", onclick: () => onOpen(f) } : { style: { cursor: "default" } },
      null, withConv ? M.findingWhere(f) : null);
    return [el("h4", {}, icon("shield", "xs"), title, el("span", { class: "count" }, fmt(fs.length))), ...fs.slice(0, limit).map(card),
      fs.length > limit ? el("div", { class: "cell-muted" }, `+ ${fmt(fs.length - limit)} more`) : null].filter(Boolean);
  }

  /* ---------------------------------------------------------- details */
  const tag = (text, attrs = {}) => el("span", { class: "tag", ...attrs }, text);
  /** the header of a detail view: icon, title with small buttons after it (copy, show in graph / timeline / nodes),
   *  chips under it, and actions plus close on the right. An action is { icon, title, run } or { icon, title, href }. */
  function head({ icon: ic, color, title, chips = [], copy = null, graph = null, timeline = null, nodes = null, actions = [], onClose = null }) {
    const sup = (i, label, onclick) => el("button", { class: "title-sup", title: label, "aria-label": label, onclick }, icon(i));
    const act = (a) => (a.href ? el("a", { class: "icon-btn sm", title: a.title, href: a.href, target: a.target || null }, icon(a.icon, "sm"))
      : el("button", { class: "icon-btn sm", title: a.title, onclick: a.run }, icon(a.icon, "sm")));
    return el("div", { class: "detail-head" },
      el("span", { class: "ico", style: { background: color } }, icon(ic)),
      el("div", { class: "grow" },
        el("div", { class: "title" }, title,
          copy ? sup("content_copy", "Copy", () => { navigator.clipboard && navigator.clipboard.writeText(copy); snack("Copied"); }) : null,
          graph ? sup("hub", "Show in graph", graph) : null,
          timeline ? sup("timeline", "Show in timeline", timeline) : null,
          nodes ? sup("table_rows", "Show in the Nodes table", nodes) : null),
        chips.length ? el("div", { class: "sub" }, ...chips.filter(Boolean)) : null),
      actions.length || onClose ? el("div", { class: "head-actions" }, ...actions.map(act),
        onClose ? el("button", { class: "icon-btn sm", title: "Close", onclick: onClose }, icon("close", "sm")) : null) : null);
  }
  /** chips describing a node: its type, mentions, paragraphs, links, conversations and list labels */
  function nodeChips(n, { paragraphs = null, links = null, convs = null } = {}) {
    const chips = [tag(M.kindOf(n).label)];
    if (n.type === "entity") chips.push(tag(plural(n.count || 0, "mention")));
    if (paragraphs !== null) chips.push(tag(plural(paragraphs, "paragraph")));
    if (links !== null) chips.push(tag(plural(links, "link")));
    if (convs !== null) chips.push(tag(plural(convs, "conversation")));
    for (const l of n.labels || []) chips.push(el("span", { class: "tag warn", title: "On an IOC / keyword list" }, icon("playlist_add_check", "xs"), l));
    return chips;
  }
  /** the icon colour of a node: its conversation's colour for a conversation node */
  const nodeColor = (n) => (n.type === "conversation" && G.convs.get(n.conv[0]) ? G.convs.get(n.conv[0]).color : M.kindOf(n).color);
  /** "Tags & comment" of a target, read-only (tagging is done from the right-click menu) */
  function annotationSection(target) {
    const view = M.annotationView(target);
    return view ? [el("h4", {}, icon("sell", "xs"), "Tags & comment"), view] : [];
  }
  /** "Seen first – last" of a node */
  function seenLine(id) {
    const [first, last] = M.seenRange(id);
    return first ? el("div", { class: "cell-muted" }, `Seen ${fmtTime(first)}${last && last !== first ? " – " + fmtTime(last) : ""}`) : null;
  }
  /** a conversation's host, user, agent, model, file, start and size (plus its harness metadata) */
  function convFacts(c) {
    return el("dl", { class: "kv" }, el("dt", {}, "Host"), el("dd", {}, c.host), el("dt", {}, "User"), el("dd", {}, c.user),
      el("dt", {}, "Agent"), el("dd", {}, c.harness), el("dt", {}, "Model"), el("dd", {}, c.model || "–"),
      el("dt", {}, "File"), el("dd", {}, c.source), el("dt", {}, "Started"), el("dd", {}, fmtTime(c.started_at) || "–"),
      el("dt", {}, "Size"), el("dd", {}, `${fmt(c.n_events)} steps · ${fmt(c.n_tool_calls)} tool calls · ${fmt(c.n_thoughts)} thoughts`),
      ...Object.entries(c.meta || {}).filter(([k]) => !["session_id", "format_version", "user_type"].includes(k))
        .flatMap(([k, v]) => [el("dt", {}, k.replace(/_/g, " ")), el("dd", {}, String(v))]));
  }

  return {
    section, scope, timeWindow, tagCounts, tagFilter, tagActions, tagCoverage, deleteTag, kindLegend, layerChips,
    findingCounts, sevChips, catChips, findingsSection, categories, head, tag, nodeChips, nodeColor, annotationSection, seenLine, convFacts,
  };
};
