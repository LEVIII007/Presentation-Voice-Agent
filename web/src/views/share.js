// Share modal: copyable public viewer link + QR (rendered via a lightweight
// inline generator-free fallback — we just show the link and copy button; QR is
// generated with a data-URI-free canvas only if the browser supports it).

import { h, toast } from "../dom.js";

export function openShareModal(deckId) {
  const url = `${window.location.origin}${window.location.pathname}#/present/${deckId}`;
  const input = h("input", { type: "text", value: url, readonly: true });

  const back = h("div", { class: "modal-back", onClick: (e) => { if (e.target === back) back.remove(); } });
  const modal = h(
    "div",
    { class: "modal share-modal" },
    h("div", { class: "eyebrow eyebrow-compact" }, "Share"),
    h("h3", {}, "Share this presentation"),
    h("p", { class: "share-copy" }, "Anyone with the link can open the talkable deck in their browser and ask questions live."),
    h("div", { class: "share-url" }, input, h("button", {
      class: "btn sm",
      onClick: async () => {
        try { await navigator.clipboard.writeText(url); toast("Link copied", "success"); }
        catch { input.select(); toast("Press ⌘/Ctrl+C to copy", "info"); }
      },
    }, "Copy")),
    h("div", { class: "share-actions" },
      h("button", { class: "btn ghost", onClick: () => back.remove() }, "Close"),
      h("a", { class: "btn", href: `#/present/${deckId}`, onClick: () => back.remove() }, "Open viewer"),
    ),
  );
  back.append(modal);
  document.body.append(back);
  input.focus();
  input.select();
}
