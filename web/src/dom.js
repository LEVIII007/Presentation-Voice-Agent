// Tiny DOM helpers so the views stay framework-free but readable.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === "dataset") {
      Object.assign(el.dataset, v);
    } else {
      el.setAttribute(k, v);
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

function createShell(node) {
  const surface = node?.dataset?.surface || (node?.classList?.contains("viewer") ? "viewer" : "product");
  const subtitle = node?.dataset?.subtitle || (surface === "viewer" ? "Live presentation" : "Voice presenter studio");
  const chrome = node?.dataset?.chrome || "full";
  const showTopbar = chrome !== "none";
  const isDashboard = node?.classList?.contains("dashboard-page");

  return h(
    "div",
    { class: `app-shell shell-${surface}${showTopbar ? "" : " shell-no-topbar"}` },
    showTopbar
      ? h(
        "header",
        { id: "topbar" },
        h(
          "a",
          { class: "brand", href: "#/" },
          h("span", { class: "brand-mark", "aria-hidden": "true" }),
          h("span", { class: "brand-sub" }, "Voice Slides"),
        ),
        h(
          "div",
          { class: "topbar-actions" },
          h("div", { class: "topbar-note" }, subtitle),
          !isDashboard ? h("a", { class: "shell-link", href: "#/" }, surface === "viewer" ? "All decks" : "Library") : null,
        ),
      )
      : null,
    h("div", { class: "app-canvas" }, node),
  );
}

export function mount(node) {
  const app = document.getElementById("app");
  app.replaceChildren(createShell(node));
}

export function navigate(hash) {
  window.location.hash = hash;
}

// Small non-blocking toast.
export function toast(msg, kind = "info") {
  let host = document.getElementById("toasts");
  if (!host) {
    host = h("div", { id: "toasts" });
    document.body.append(host);
  }
  const t = h("div", { class: `toast ${kind}` }, msg);
  host.append(t);
  setTimeout(() => t.classList.add("show"), 10);
  setTimeout(() => {
    t.classList.remove("show");
    setTimeout(() => t.remove(), 300);
  }, 3600);
}
