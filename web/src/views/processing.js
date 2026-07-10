// Processing screen: polls /status every 1.5s and shows a live thumbnail grid
// that fills in per slide. Fully refresh-proof — the server owns all state, so
// reloading just re-reads /status and rehydrates. Handles failed + done states.

import { api } from "../api.js";
import { h, mount, navigate, toast } from "../dom.js";

const POLL_MS = 1500;

const STAGES = {
  uploaded: "Queued for processing…",
  processing: "Reading slides, rendering images, writing narration…",
  ready: "Done!",
  failed: "Ingestion failed.",
};

export async function renderProcessing(deckId) {
  let stopped = false;
  const page = h("div", { class: "page" });
  mount(page);

  page.append(
    h("div", { class: "crumb" }, h("a", { href: "#/" }, "← All decks")),
    h("div", { class: "page-head" }, h("div", {}, h("h2", {}, "Preparing your deck"), h("div", { class: "sub", id: "p-sub" }, "Connecting…"))),
  );

  const bar = h("div", { class: "bar" }, h("span", { style: "width:0%" }));
  const stageLabel = h("div", { class: "stage-label" }, "");
  const thumbs = h("div", { class: "thumbs" });
  const actions = h("div", { style: "margin-top:24px;display:flex;gap:10px" });
  page.append(bar, stageLabel, thumbs, actions);

  const cells = new Map();
  function ensureCells(count) {
    if (cells.size === count) return;
    thumbs.replaceChildren();
    cells.clear();
    for (let n = 1; n <= count; n++) {
      const cell = h("div", { class: "thumb skeleton" }, h("span", { class: "n" }, n));
      cells.set(n, cell);
      thumbs.append(cell);
    }
  }

  function paintSlide(s) {
    const cell = cells.get(s.number);
    if (!cell) return;
    if (s.status === "failed") {
      cell.className = "thumb failed";
      cell.replaceChildren(h("span", { class: "n" }, s.number), h("span", { class: "badge" }, "⚠"));
      return;
    }
    if (s.has_image) {
      const badge = s.status === "ready" ? "✓" : "…";
      cell.className = "thumb";
      cell.replaceChildren(
        h("img", { src: `${api.imageUrl(deckId, s.number)}?t=${Date.now()}`, alt: `Slide ${s.number}`, loading: "lazy" }),
        h("span", { class: "n" }, s.number),
        h("span", { class: "badge" }, badge),
      );
    }
  }

  async function poll() {
    if (stopped) return;
    try {
      const st = await api.status(deckId);
      const total = st.slide_count || st.counts.total || 0;
      document.getElementById("p-sub").textContent =
        total ? `${st.counts.narrated} of ${total} slides ready` : "Starting…";
      stageLabel.textContent = STAGES[st.status] || st.status;

      if (total) ensureCells(total);
      st.slides.forEach(paintSlide);

      const pct = total ? Math.round((st.counts.narrated / total) * 100) : 0;
      bar.firstChild.style.width = `${pct}%`;

      if (st.status === "ready") {
        stopped = true;
        toast("Deck ready!", "success");
        setTimeout(() => navigate(`#/deck/${deckId}/review`), 500);
        return;
      }
      if (st.status === "failed") {
        stopped = true;
        actions.replaceChildren(
          h("div", { class: "banner error", style: "flex:1" }, `Ingestion failed: ${st.error || "unknown error"}`),
        );
        actions.append(
          h("button", { class: "btn", onClick: async () => {
            try { await api.retryDeck(deckId); stopped = false; actions.replaceChildren(); poll(); }
            catch (e) { toast(e.message, "error"); }
          } }, "Retry"),
          h("button", { class: "btn ghost", onClick: () => navigate("#/") }, "Back"),
        );
        return;
      }
      setTimeout(poll, POLL_MS);
    } catch (e) {
      if (String(e.message).includes("not found") || String(e.message).includes("404")) {
        stopped = true;
        page.replaceChildren(h("div", { class: "banner error" }, "Deck not found."), h("a", { href: "#/" }, "← All decks"));
        return;
      }
      stageLabel.textContent = "Reconnecting…";
      setTimeout(poll, POLL_MS * 2);
    }
  }
  poll();

  return () => { stopped = true; };
}
