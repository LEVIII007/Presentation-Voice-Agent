// Review & edit: the human-in-the-loop screen. Each slide's rendered image sits
// next to its editable title + AI narration. Edits autosave (debounced PATCH).
// Low-confidence / failed slides are flagged so vision misreads get caught here.

import { api } from "../api.js";
import { h, mount, navigate, toast } from "../dom.js";
import { openShareModal } from "./share.js";

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function slideRow(deckId, slide) {
  const hint = h("div", { class: "save-hint" }, "");
  const flag = slide.status === "failed" ? h("div", { class: "flag" }, "⚠ Narration failed for this slide — please fill it in.") : null;

  const save = debounce(async (field, value) => {
    hint.textContent = "Saving…";
    hint.classList.remove("saved");
    try {
      await api.patchSlide(deckId, slide.number, { [field]: value });
      hint.textContent = "Saved";
      hint.classList.add("saved");
    } catch (e) {
      hint.textContent = `Save failed: ${e.message}`;
    }
  }, 700);

  const titleInput = h("input", { type: "text", value: slide.title || "" });
  titleInput.addEventListener("input", () => save("title", titleInput.value));

  const notesArea = h("textarea", { rows: "5" }, slide.notes || "");
  notesArea.addEventListener("input", () => save("notes", notesArea.value));

  const img = slide.has_image
    ? h("img", { src: api.imageUrl(deckId, slide.number), alt: `Slide ${slide.number}`, loading: "lazy" })
    : h("div", { style: "display:flex;align-items:center;justify-content:center;height:100%;color:var(--faint);font-size:13px" }, "No image");

  return h(
    "div",
    { class: "review-row" },
    h("div", {}, h("div", { class: "slide-num", style: "margin-bottom:8px" }, `Slide ${slide.number}`), h("div", { class: "review-img" }, img)),
    h(
      "div",
      { class: "review-fields" },
      flag,
      h("label", {}, "Title"),
      titleInput,
      h("label", {}, "Speaker notes (what the AI says & answers from)"),
      notesArea,
      hint,
    ),
  );
}

export async function renderReview(deckId) {
  const page = h("div", { class: "page" });
  mount(page);
  page.append(h("div", { class: "crumb" }, h("a", { href: "#/" }, "← All decks")), h("div", { class: "spinner" }));

  let deck;
  try {
    deck = await api.getDeck(deckId);
  } catch (e) {
    page.replaceChildren(h("div", { class: "banner error" }, `Could not load deck: ${e.message}`), h("a", { href: "#/" }, "← All decks"));
    return;
  }
  if (deck.status !== "ready") {
    navigate(`#/deck/${deckId}/processing`);
    return;
  }

  const failed = deck.slides.filter((s) => s.status === "failed").length;
  const header = [
    h("div", { class: "crumb" }, h("a", { href: "#/" }, "← All decks")),
    h(
      "div",
      { class: "page-head" },
      h("div", {}, h("h2", {}, deck.title), h("div", { class: "sub" }, `${deck.slides.length} slides · review the AI's narration, then present or share`)),
      h(
        "div",
        { style: "display:flex;gap:10px" },
        h("button", { class: "btn ghost", onClick: () => navigate(`#/present/${deckId}`) }, "▶ Preview as viewer"),
        h("button", { class: "btn", onClick: () => openShareModal(deckId) }, "Share"),
      ),
    ),
  ];
  if (failed) header.push(h("div", { class: "banner info" }, `${failed} slide(s) need attention — look for the ⚠ flags below.`));
  header.push(h("div", { class: "banner info" }, "Edits save automatically. The presenter speaks and answers questions from these notes."));
  page.replaceChildren(...header);

  const list = h("div", {});
  deck.slides.forEach((s) => list.append(slideRow(deckId, s)));
  page.append(list);
}
