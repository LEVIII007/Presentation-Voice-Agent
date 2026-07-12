// Dashboard: deck library + upload. Empty state guides first-time users.

import { api, fmtDate } from "../api.js";
import { h, mount, navigate, toast } from "../dom.js";

const ACCEPT = ".pdf,.pptx";

function statusPill(status) {
  const label = { ready: "Ready", processing: "Processing", uploaded: "Queued", failed: "Failed" }[status] || status;
  const live = status === "processing" || status === "uploaded";
  return h("span", { class: `pill ${status}${live ? " live" : ""}` }, h("span", { class: "dot" }), label);
}

function featureChip(text) {
  return h("span", { class: "feature-chip" }, text);
}

function deckCard(deck, onDelete) {
  const target =
    deck.status === "ready"
      ? `#/deck/${deck.id}/review`
      : deck.status === "failed"
        ? `#/deck/${deck.id}/processing`
        : `#/deck/${deck.id}/processing`;

  const statusCopy = {
    ready: "Ready to review, share, and present live.",
    processing: "Rendering slides and drafting narration now.",
    uploaded: "Queued to start processing on the server.",
    failed: "Processing stopped early. You can retry and continue.",
  }[deck.status] || "Open to continue.";

  const actions = h("div", { class: "card-actions" });
  if (deck.status === "ready") {
    actions.append(
      h("button", { class: "btn sm", onClick: (e) => { e.stopPropagation(); navigate(`#/present/${deck.id}`); } }, "▶ Present"),
      h("button", { class: "btn ghost sm", onClick: (e) => { e.stopPropagation(); navigate(`#/deck/${deck.id}/review`); } }, "Edit"),
    );
  } else {
    actions.append(h("button", { class: "btn ghost sm", onClick: (e) => { e.stopPropagation(); navigate(target); } }, "View progress"));
  }
  actions.append(
    h("button", {
      class: "btn danger sm", style: "margin-left:auto",
      onClick: async (e) => { e.stopPropagation(); onDelete(deck); },
    }, "Delete"),
  );

  return h(
    "article",
    { class: "card deck-card link", onClick: () => navigate(target) },
    h("div", { class: "row card-head" }, statusPill(deck.status), h("span", { class: "meta" }, fmtDate(deck.created_at))),
    h("div", { class: "title" }, deck.title || deck.source_filename),
    h("div", { class: "deck-card-copy" }, statusCopy),
    h(
      "div",
      { class: "deck-card-meta" },
      h("span", { class: "meta-chip" }, deck.slide_count ? `${deck.slide_count} slides` : "Counting slides"),
      deck.status === "failed" && deck.error
        ? h("span", { class: "meta-chip meta-chip-warn" }, deck.error)
        : null,
    ),
    actions,
  );
}

function uploadZone(onFile) {
  const input = h("input", { type: "file", accept: ACCEPT, style: "display:none" });
  input.addEventListener("change", () => input.files[0] && onFile(input.files[0]));

  const zone = h(
    "div",
    { class: "dropzone", onClick: () => input.click() },
    h("div", { class: "dropzone-orb", "aria-hidden": "true" }),
    h("div", { class: "icon" }, "Upload"),
    h("div", { class: "big" }, "Drop a PDF or PPTX, or click to browse"),
    h("div", { class: "hint" }, "Up to 40 MB and 60 slides. Export Keynote decks to PDF first."),
    input,
  );
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { stop(e); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { stop(e); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", (e) => e.dataTransfer.files[0] && onFile(e.dataTransfer.files[0]));
  return zone;
}

export async function renderDashboard() {
  const page = h("div", {
    class: "page dashboard-page",
    dataset: { surface: "product", subtitle: "Voice presenter studio" },
  });
  mount(page);

  const progressWrap = h("div", { class: "upload-progress", style: "display:none" });
  const progressBar = h("div", { class: "bar" }, h("span", { style: "width:0%" }));
  const progressLabel = h("div", { class: "stage-label" }, "");
  progressWrap.append(
    h("div", { class: "upload-progress-head" },
      h("div", { class: "eyebrow eyebrow-compact" }, "Upload status"),
      progressLabel,
    ),
    progressBar,
  );

  async function handleFile(file) {
    const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
    if (!ACCEPT.includes(ext)) return toast("Only PDF and PPTX files are supported", "error");
    if (file.size > 40 * 1024 * 1024) return toast("File exceeds the 40 MB limit", "error");

    progressWrap.style.display = "block";
    progressLabel.textContent = `Uploading ${file.name}…`;
    try {
      const deck = await api.upload(file, {
        onProgress: (frac) => {
          progressBar.firstChild.style.width = `${Math.round(frac * 100)}%`;
        },
      });
      toast("Uploaded — starting ingestion", "success");
      navigate(`#/deck/${deck.id}/processing`);
    } catch (e) {
      progressWrap.style.display = "none";
      toast(e.message, "error");
    }
  }

  const zone = uploadZone(handleFile);
  const hero = h(
    "section",
    { class: "dashboard-hero" },
    h(
      "div",
      { class: "dashboard-hero-copy" },
      h("div", { class: "eyebrow" }, "Voice-presented decks"),
      h("h1", { class: "hero-title" }, "Give every deck a voice."),
      h(
        "p",
        { class: "hero-body" },
        "Upload a PDF or PPTX, let the system draft slide-by-slide narration, then turn the deck into a live presenter that can be interrupted, questioned, and shared.",
      ),
      h(
        "div",
        { class: "hero-feature-row" },
        featureChip("Upload once"),
        featureChip("Review speaker notes"),
        featureChip("Present live and share"),
      ),
    ),
    h(
      "div",
      { class: "dashboard-hero-visual" },
      h("div", { class: "hero-orb", "aria-hidden": "true" }),
      h(
        "div",
        { class: "hero-signal-card" },
        h("div", { class: "signal-label" }, "Workflow"),
        h("div", { class: "signal-title" }, "Upload. Review. Present."),
        h(
          "div",
          { class: "signal-copy" },
          "Built for teams who want premium decks, live narration, and fast handoff from authoring to audience questions.",
        ),
        h(
          "div",
          { class: "signal-steps" },
          h("span", { class: "signal-step" }, "PDF or PPTX"),
          h("span", { class: "signal-step" }, "AI notes"),
          h("span", { class: "signal-step" }, "Shareable viewer"),
        ),
      ),
    ),
  );

  const uploadSection = h(
    "section",
    { class: "dashboard-upload-panel" },
    h(
      "div",
      { class: "section-intro" },
      h("h4", { class: "section-title" }, "Start a new presentation"),
      h(
        "p",
        { class: "section-copy" },
        "Drop in a deck and we will prepare a talkable version.",
      ),
    ),
    zone,
    progressWrap,
  );

  const listWrap = h("div", { class: "library-grid-wrap" }, h("div", { class: "spinner" }));
  const librarySection = h(
    "section",
    { class: "library-section" },
    h(
      "div",
      { class: "library-head" },
      h(
        "div",
        {},
        h("h4", { class: "section-title" }, "Your decks"),
      ),
    ),
    listWrap,
  );

  page.append( uploadSection, librarySection);

  async function refreshList() {
    try {
      const { decks } = await api.listDecks();
      const ready = decks.filter((deck) => deck.status === "ready").length;
      if (!decks.length) {
        listWrap.replaceChildren(
          h(
            "div",
            { class: "empty library-empty" },
            h("div", { class: "empty-orb", "aria-hidden": "true" }),
            h("h3", {}, "No decks yet"),
            h("div", {}, "Upload one above to turn it into a polished, talkable presentation."),
          ),
        );
        return;
      }
      const grid = h("div", { class: "grid" });
      for (const d of decks) {
        grid.append(
          deckCard(d, async (deck) => {
            if (!confirm(`Delete "${deck.title}"? This can't be undone.`)) return;
            try {
              await api.deleteDeck(deck.id);
              toast("Deck deleted", "success");
              refreshList();
            } catch (e) {
              toast(e.message, "error");
            }
          }),
        );
      }
      listWrap.replaceChildren(grid);
    } catch (e) {
      libraryStat.textContent = "Waiting for the backend";
      listWrap.replaceChildren(h("div", { class: "banner error" }, `Backend not reachable — start the server, then reload. (${e.message})`));
    }
  }
  refreshList();
}
