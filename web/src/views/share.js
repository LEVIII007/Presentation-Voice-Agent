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
    { class: "modal" },
    h("h3", {}, "Share this presentation"),
    h("p", { class: "sub", style: "color:var(--muted);font-size:14px" }, "Anyone with the link can open the talkable deck in their browser."),
    h("div", { class: "share-url" }, input, h("button", {
      class: "btn sm",
      onClick: async () => {
        try { await navigator.clipboard.writeText(url); toast("Link copied", "success"); }
        catch { input.select(); toast("Press ⌘/Ctrl+C to copy", "info"); }
      },
    }, "Copy")),
    h("div", { style: "display:flex;gap:10px;justify-content:flex-end;margin-top:16px" },
      h("button", { class: "btn ghost", onClick: () => back.remove() }, "Close"),
      h("a", { class: "btn", href: `#/present/${deckId}`, onClick: () => back.remove() }, "Open viewer"),
    ),
  );
  back.append(modal);
  document.body.append(back);
  input.focus();
  input.select();
}
