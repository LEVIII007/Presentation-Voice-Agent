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
  const flag = slide.status === "failed" ? h("div", { class: "flag" }, "Narration failed for this slide. Please fill it in before presenting.") : null;

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
    "article",
    { class: "review-row" },
    h(
      "div",
      { class: "review-media" },
      h(
        "div",
        { class: "review-media-head" },
        h("div", { class: "slide-num review-slide-num" }, `Slide ${slide.number}`),
        slide.status === "failed"
          ? h("span", { class: "mini-status mini-status-failed" }, "Needs attention")
          : h("span", { class: "mini-status" }, "Draft ready"),
      ),
      h("div", { class: "review-img" }, img),
    ),
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
  const page = h("div", {
    class: "page review-page",
    dataset: { surface: "product", subtitle: "Narration review" },
  });
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
      "section",
      { class: "review-hero" },
      h(
        "div",
        { class: "review-hero-copy" },
        h("div", { class: "eyebrow" }, "Narration review"),
        h("h1", { class: "section-title" }, deck.title),
        h(
          "p",
          { class: "section-copy" },
          `${deck.slides.length} slides. Fine-tune what the presenter says, then preview or share the live deck.`,
        ),
        h(
          "div",
          { class: "review-stats" },
          h("span", { class: "meta-chip" }, `${deck.slides.length} slides`),
        ),
      ),
      h(
        "div",
        { class: "review-hero-actions" },
        h("button", { class: "btn ghost", onClick: () => navigate(`#/present/${deckId}`) }, "▶ Start Presentation"),
      ),
    ),
  ];
  if (failed) header.push(h("div", { class: "banner info" }, `${failed} slide(s) need attention. Review the marked slides before presenting.`));
  header.push(h("div", { class: "banner info" }, "Edits save automatically. The presenter speaks and answers questions from these notes."));
  page.replaceChildren(...header);

  const list = h("div", { class: "review-list" });
  deck.slides.forEach((s) => list.append(slideRow(deckId, s)));
  page.append(list);
}
