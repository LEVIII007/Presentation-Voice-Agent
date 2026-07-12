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
  const page = h("div", {
    class: "page processing-page",
    dataset: { surface: "product", subtitle: "Deck ingestion" },
  });
  mount(page);

  const progressSub = h("div", { class: "sub processing-sub", id: "p-sub" }, "Connecting…");
  const stageLabel = h("div", { class: "stage-label processing-stage-label" }, "");
  const progressPct = h("div", { class: "processing-percent" }, "0%");
  const progressCount = h("div", { class: "processing-count" }, "Waiting for slides…");

  page.append(
    h(
      "section",
      { class: "processing-hero" },
      h(
        "div",
        { class: "processing-copy" },
        h("div", { class: "crumb" }, h("a", { href: "#/" }, "← All decks")),
        h("div", { class: "eyebrow" }, "Deck ingestion"),
        h("h1", { class: "section-title" }, "Preparing your deck"),
        progressSub,
      ),
      h(
        "div",
        { class: "processing-sidecard" },
        h("div", { class: "processing-side-label" }, "What happens now"),
        h("div", { class: "processing-side-step" }, "1. Slides are rendered into crisp images."),
        h("div", { class: "processing-side-step" }, "2. The AI drafts narration for each slide."),
        h("div", { class: "processing-side-step" }, "3. You review and fine-tune before presenting."),
      ),
    ),
  );

  const bar = h("div", { class: "bar" }, h("span", { style: "width:0%" }));
  const thumbs = h("div", { class: "thumbs" });
  const actions = h("div", { class: "processing-actions" });
  page.append(
    h(
      "section",
      { class: "processing-panel" },
      h(
        "div",
        { class: "processing-panel-head" },
        h("div", { class: "processing-panel-copy" }, stageLabel, progressCount),
        progressPct,
      ),
      bar,
      h("div", { class: "processing-panel-note" }, "Refresh-proof progress. You can leave and come back; the server keeps the deck state."),
    ),
    h(
      "section",
      { class: "processing-thumbs-shell" },
      h(
        "div",
        { class: "section-head-inline" },
        h("div", { class: "eyebrow eyebrow-compact" }, "Slide progress"),
        h("div", { class: "processing-thumbs-copy" }, "Rendered thumbnails appear here as each slide finishes."),
      ),
      thumbs,
    ),
    actions,
  );

  const cells = new Map();
  function createCell(n) {
    const num = h("span", { class: "n" }, n);
    const badge = h("span", { class: "badge" });
    const cell = h("div", { class: "thumb skeleton" }, num, badge);
    return { cell, badge, img: null, stateKey: "pending:empty" };
  }

  function ensureCells(count) {
    for (let n = cells.size + 1; n <= count; n++) {
      const view = createCell(n);
      cells.set(n, view);
      thumbs.append(view.cell);
    }
  }

  function ensureImage(view, number) {
    if (view.img) return view.img;
    view.img = h("img", { src: api.imageUrl(deckId, number), alt: `Slide ${number}`, loading: "lazy" });
    view.cell.prepend(view.img);
    return view.img;
  }

  function paintSlide(s) {
    const view = cells.get(s.number);
    if (!view) return;
    const stateKey = `${s.status}:${s.has_image ? "image" : "empty"}`;
    if (view.stateKey === stateKey) return;
    view.stateKey = stateKey;

    // Keep each tile mounted across polls so completed thumbnails do not flash.
    view.cell.className = "thumb";

    if (s.status === "failed") {
      if (s.has_image) ensureImage(view, s.number);
      view.cell.classList.add("failed");
      view.badge.textContent = "⚠";
      return;
    }

    if (!s.has_image) {
      view.cell.classList.add("skeleton");
      view.badge.textContent = "";
      return;
    }

    ensureImage(view, s.number);
    view.badge.textContent = s.status === "ready" ? "✓" : "…";
  }

  async function poll() {
    if (stopped) return;
    try {
      const st = await api.status(deckId);
      const total = st.slide_count || st.counts.total || 0;
      progressSub.textContent =
        total ? `${st.counts.narrated} of ${total} slides ready` : "Starting…";
      stageLabel.textContent = STAGES[st.status] || st.status;

      if (total) ensureCells(total);
      st.slides.forEach(paintSlide);

      const pct = total ? Math.round((st.counts.narrated / total) * 100) : 0;
      bar.firstChild.style.width = `${pct}%`;
      progressPct.textContent = `${pct}%`;
      progressCount.textContent = total ? `${st.counts.narrated} of ${total} slides narrated` : "Waiting for slide count…";

      if (st.status === "ready") {
        stopped = true;
        toast("Deck ready!", "success");
        setTimeout(() => navigate(`#/deck/${deckId}/review`), 500);
        return;
      }
      if (st.status === "failed") {
        stopped = true;
        actions.replaceChildren(
          h("div", { class: "banner error processing-banner" }, `Ingestion failed: ${st.error || "unknown error"}`),
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
