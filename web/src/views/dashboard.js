// Dashboard: deck library + upload. Empty state guides first-time users.

import { api, fmtDate } from "../api.js";
import { h, mount, navigate, toast } from "../dom.js";

const ACCEPT = ".pdf,.pptx";

function statusPill(status) {
  const label = { ready: "Ready", processing: "Processing", uploaded: "Queued", failed: "Failed" }[status] || status;
  const live = status === "processing" || status === "uploaded";
  return h("span", { class: `pill ${status}${live ? " live" : ""}` }, h("span", { class: "dot" }), label);
}

function deckCard(deck, onDelete) {
  const target =
    deck.status === "ready"
      ? `#/deck/${deck.id}/review`
      : deck.status === "failed"
        ? `#/deck/${deck.id}/processing`
        : `#/deck/${deck.id}/processing`;

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
    "div",
    { class: "card link", onClick: () => navigate(target) },
    h("div", { class: "row" }, statusPill(deck.status), h("span", { class: "meta" }, fmtDate(deck.created_at))),
    h("div", { class: "title" }, deck.title || deck.source_filename),
    h("div", { class: "meta" }, deck.slide_count ? `${deck.slide_count} slides` : "—", deck.status === "failed" && deck.error ? ` · ${deck.error}` : ""),
    actions,
  );
}

function uploadZone(onFile) {
  const input = h("input", { type: "file", accept: ACCEPT, style: "display:none" });
  input.addEventListener("change", () => input.files[0] && onFile(input.files[0]));

  const zone = h(
    "div",
    { class: "dropzone", onClick: () => input.click() },
    h("div", { class: "icon" }, "⬆"),
    h("div", { class: "big" }, "Drop a PDF or PPTX, or click to browse"),
    h("div", { class: "hint" }, "Up to 40 MB · 60 slides. Keynote/PPT → export to PDF first."),
    input,
  );
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { stop(e); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { stop(e); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", (e) => e.dataTransfer.files[0] && onFile(e.dataTransfer.files[0]));
  return zone;
}

export async function renderDashboard() {
  const page = h("div", { class: "page" });
  mount(page);

  const head = h(
    "div",
    { class: "page-head" },
    h("div", {}, h("h2", {}, "Your decks"), h("div", { class: "sub" }, "Upload a deck and it becomes a slideshow you can talk to.")),
  );
  page.append(head);

  const progressWrap = h("div", { style: "display:none;margin-bottom:20px" });
  const progressBar = h("div", { class: "bar" }, h("span", { style: "width:0%" }));
  const progressLabel = h("div", { class: "stage-label" }, "");
  progressWrap.append(progressLabel, progressBar);

  async function handleFile(file) {
    const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
    if (!ACCEPT.includes(ext)) return toast("Only PDF and PPTX files are supported", "error");
    if (file.size > 40 * 1024 * 1024) return toast("File exceeds the 40 MB limit", "error");

    progressWrap.style.display = "block";
    progressLabel.textContent = `Uploading ${file.name}…`;
    try {
      const deck = await api.upload(file, (frac) => {
        progressBar.firstChild.style.width = `${Math.round(frac * 100)}%`;
      });
      toast("Uploaded — starting ingestion", "success");
      navigate(`#/deck/${deck.id}/processing`);
    } catch (e) {
      progressWrap.style.display = "none";
      toast(e.message, "error");
    }
  }

  const zone = uploadZone(handleFile);
  page.append(zone, progressWrap);

  const listWrap = h("div", { style: "margin-top:28px" }, h("div", { class: "spinner" }));
  page.append(listWrap);

  async function refreshList() {
    try {
      const { decks } = await api.listDecks();
      if (!decks.length) {
        listWrap.replaceChildren(
          h("div", { class: "empty" }, h("div", { class: "icon" }, "🎞"), h("h3", {}, "No decks yet"), h("div", {}, "Upload one above to get started.")),
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
      listWrap.replaceChildren(h("div", { class: "banner error" }, `Backend not reachable — start the server, then reload. (${e.message})`));
    }
  }
  refreshList();
}
