/* Shared helpers for the graph page and the settings page. */
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

  const fmt = (n) => Number(n).toLocaleString();
  const plural = (n, word, many) => `${fmt(n)} ${n === 1 ? word : many || word + "s"}`;

  return { store, api, esc, el, icon, applyTheme, effectiveTheme, cssVar, snack, debounce, fmt, plural };
})();
