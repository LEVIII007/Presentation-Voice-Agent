import "./styles.css";

import { h, mount } from "./dom.js";
import { renderDashboard } from "./views/dashboard.js";
import { renderLatency } from "./views/latency.js";
import { renderProcessing } from "./views/processing.js";
import { renderReview } from "./views/review.js";
import { renderViewer } from "./views/viewer.js";

// Hash routes:
//   #/                       -> dashboard (library + upload)
//   #/deck/:id/processing    -> ingestion progress (poll)
//   #/deck/:id/review        -> creator review & edit
//   #/present/:id            -> viewer (shareable, talkable deck)

let disposeCurrent = null;

async function route() {
  if (typeof disposeCurrent === "function") {
    try { disposeCurrent(); } catch {}
    disposeCurrent = null;
  }

  const hash = window.location.hash.replace(/^#/, "") || "/";
  const parts = hash.split("/").filter(Boolean); // e.g. ["deck","abc","review"]

  try {
    if (parts.length === 0) {
      disposeCurrent = await renderDashboard();
    } else if (parts[0] === "latency") {
      disposeCurrent = await renderLatency();
    } else if (parts[0] === "present" && parts[1]) {
      disposeCurrent = await renderViewer(parts[1]);
    } else if (parts[0] === "deck" && parts[1]) {
      const sub = parts[2] || "review";
      if (sub === "processing") disposeCurrent = await renderProcessing(parts[1]);
      else disposeCurrent = await renderReview(parts[1]);
    } else {
      window.location.hash = "#/";
    }
  } catch (e) {
    console.error("Route error:", e);
    mount(
      h(
        "div",
        { class: "page", dataset: { surface: "product", subtitle: "Something went wrong" } },
        h("div", { class: "banner error" }, `Something went wrong: ${e.message}`),
        h("a", { href: "#/" }, "← Back"),
      ),
    );
  }
}

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);
route();
