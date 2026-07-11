// The viewer — the live talkable deck. This is the shareable, mobile-friendly
// screen. Renders each slide (image if ingested, else title+bullets), a slide
// rail for manual nav, live captions, starter chips, a typed-question fallback,
// mic priming, thinking indicator, and an end screen with the creator CTA.

import { api, escapeHtml } from "../api.js";
import { h, mount, toast } from "../dom.js";
import { createVoiceSession } from "../voice.js";

const STARTERS = ["Give me the overview", "What's the most important point?", "Summarize this in one line"];

const STATUS_TEXT = {
  connecting: "Connecting…",
  connected: "Listening",
  paused: "Paused",
  speaking: "Presenter speaking",
  listening: "You're speaking",
  thinking: "Thinking…",
  offline: "Offline",
  error: "Error",
};

export async function renderViewer(deckId) {
  mount(h("div", { class: "page" }, h("div", { class: "spinner" })));

  let deck;
  try {
    deck = await api.getDeck(deckId);
  } catch (e) {
    mount(h("div", { class: "page" }, h("div", { class: "banner error" }, `Could not load deck: ${e.message}`), h("a", { href: "#/" }, "← All decks")));
    return;
  }
  if (deck.status !== "ready") {
    mount(h("div", { class: "page" },
      h("div", { class: "banner info" }, "This deck isn't ready to present yet."),
      h("a", { class: "btn", href: `#/deck/${deckId}/processing` }, "See progress")));
    return;
  }

  const slides = deck.slides;
  const total = slides.length;
  let current = 1;
  let captionsOn = true;
  let presentationPaused = false;
  let flowBusy = false;
  let session = null;
  let cleanup = () => {};
  let statusState = "offline";
  let statusLabel = STATUS_TEXT.offline;

  // --- Elements ---
  const rail = h("div", { class: "rail" });
  const stageInner = h("div", { class: "stage-inner" });
  const slideCard = h("div", { class: "slide-card" });
  stageInner.append(slideCard);
  const breadcrumb = h("div", { class: "nav-breadcrumb" }, "");
  const prevBtn = h("button", { class: "stage-nav prev", title: "Previous slide", onClick: () => go(current - 1, true) }, "‹");
  const nextBtn = h("button", { class: "stage-nav next", title: "Next slide", onClick: () => go(current + 1, true) }, "›");
  const stageFlowBtn = h("button", { class: "btn ghost stage-action stage-flow", type: "button", onClick: togglePresentationFlow }, "Pause");
  const stageStopBtn = h("button", { class: "btn danger stage-action stage-stop", type: "button", onClick: stopSession }, "Stop");
  const stageActions = h("div", { class: "stage-actions" }, stageFlowBtn, stageStopBtn);
  stageActions.hidden = true;
  const stage = h("div", { class: "stage" }, prevBtn, breadcrumb, stageInner, nextBtn, stageActions);

  const statusPill = h("span", { class: "pill" }, h("span", { class: "dot" }), h("span", { id: "st-text" }, "Offline"));
  const caption = h("div", { class: "caption" }, "Press Start and allow the microphone to begin — or type your question below.");
  const chips = h("div", { class: "chips" });
  STARTERS.forEach((q) => chips.append(h("button", { class: "chip", onClick: () => ask(q) }, q)));

  const textInput = h("input", { type: "text", placeholder: "Type a question instead…" });
  const textForm = h("form", { class: "textbar", onSubmit: (e) => { e.preventDefault(); const v = textInput.value.trim(); if (v) { ask(v); textInput.value = ""; } } },
    textInput, h("button", { class: "btn sm", type: "submit" }, "Send"));

  const startBtn = h("button", { class: "btn big", onClick: toggleConnect }, "▶ Start presentation");
  const capToggle = h("span", { class: "toggle-cap", onClick: () => { captionsOn = !captionsOn; capToggle.textContent = captionsOn ? "Captions: on" : "Captions: off"; if (!captionsOn) caption.textContent = ""; } }, "Captions: on");

  const controls = h("div", { class: "dock-controls" }, startBtn, capToggle);
  const dock = h("div", { class: "dock" },
    h("div", { class: "dock-status" }, statusPill),
    caption, chips, textForm, controls,
    h("div", { class: "hint", style: "font-size:12px;color:var(--faint)" }, "Tip: just start talking to interrupt the presenter. Use headphones to avoid echo."),
  );

  const viewer = h("div", { class: "viewer" }, rail, stage, dock);
  mount(viewer);

  // --- Rail ---
  slides.forEach((s) => {
    const t = h("div", { class: "rail-thumb", dataset: { n: s.number }, title: s.title || `Slide ${s.number}`, onClick: () => go(s.number, true) },
      s.has_image ? h("img", { src: api.imageUrl(deckId, s.number), alt: "", loading: "lazy" }) : h("div", { style: "display:flex;align-items:center;justify-content:center;height:100%;font-size:11px;color:var(--faint);padding:4px;text-align:center" }, s.title || `Slide ${s.number}`),
      h("span", { class: "rn" }, s.number));
    rail.append(t);
  });

  function renderSlide(n) {
    const s = slides[n - 1];
    if (!s) return;
    current = n;
    slideCard.classList.add("swap");
    setTimeout(() => {
      if (s.has_image) {
        slideCard.replaceChildren(h("img", { src: api.imageUrl(deckId, n), alt: s.title || `Slide ${n}` }));
      } else {
        const ul = h("ul", { class: "slide-bullets" });
        (s.bullets || []).forEach((b) => ul.append(h("li", {}, b)));
        slideCard.replaceChildren(
          h("div", { class: "slide-num" }, `Slide ${n} of ${total}`),
          h("h2", { class: "slide-title" }, s.title || `Slide ${n}`),
          ul,
        );
      }
      slideCard.classList.remove("swap");
    }, 180);
    [...rail.children].forEach((c) => c.classList.toggle("active", Number(c.dataset.n) === n));
    const active = rail.children[n - 1];
    if (active) active.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  // Navigate. `manual` = user drove (rail/arrows/keys) vs agent-driven.
  function go(n, manual) {
    n = Math.max(1, Math.min(total, n));
    if (n === current && manual) return;
    renderSlide(n);
    if (manual && session && session.connected) {
      session.notifyManualSlide(n, slides[n - 1].title);
      showBreadcrumb("You jumped here");
    }
  }

  function showBreadcrumb(text) {
    breadcrumb.textContent = text;
    breadcrumb.classList.add("show");
    clearTimeout(showBreadcrumb._t);
    showBreadcrumb._t = setTimeout(() => breadcrumb.classList.remove("show"), 2200);
  }

  function renderStatus() {
    let state = statusState;
    let text = statusLabel || STATUS_TEXT[state] || "";
    if (presentationPaused && state === "connected") {
      state = "paused";
      text = STATUS_TEXT.paused;
    }
    statusPill.className = `pill ${state}${["speaking", "listening", "thinking", "connected"].includes(state) ? " live" : ""}`;
    document.getElementById("st-text").textContent = text;
  }

  function setStatus(state, text) {
    statusState = state;
    statusLabel = text || STATUS_TEXT[state] || "";
    renderStatus();
  }

  function setCaption(html) {
    if (captionsOn) caption.innerHTML = html;
  }

  function syncSessionControls({ live = false, busy = false } = {}) {
    startBtn.disabled = busy;
    startBtn.textContent = live ? "■ End presentation" : "▶ Start presentation";
    startBtn.classList.toggle("live", live);
    stageActions.hidden = !live;
    stageStopBtn.disabled = busy || flowBusy;
    stageFlowBtn.disabled = busy || flowBusy;
    stageFlowBtn.textContent = presentationPaused ? "Resume" : "Pause";
    stageFlowBtn.classList.toggle("paused", presentationPaused);
  }

  function ask(text) {
    if (!session || !session.connected) {
      toast("Press Start first to ask by voice or text", "info");
      return;
    }
    setCaption(`<b>You:</b> ${escapeHtml(text)}`);
    setStatus("thinking", "Thinking…");
    session.sendText(text);
  }

  // --- Connect / disconnect ---
  async function toggleConnect() {
    if (session && session.connected) {
      await stopSession();
      return;
    }
    await startSession(true);
  }

  async function togglePresentationFlow() {
    if (!session || !session.connected || flowBusy) return;
    const action = presentationPaused ? "resume" : "pause";
    const nextPaused = action === "pause";
    flowBusy = true;
    syncSessionControls({ live: true });
    try {
      const sent = session.setPresentationFlow(action);
      if (!sent) throw new Error(`Could not ${action} the presentation right now.`);
      presentationPaused = nextPaused;
      renderStatus();
      setCaption(
        presentationPaused
          ? "Presentation paused. Ask a question or resume when you're ready."
          : "Presentation resumed. It will continue from where it left off.",
      );
    } catch (e) {
      setCaption(`Could not ${action} the presentation: ${String(e?.message || e)}`);
      toast(`Could not ${action} the presentation. Please try again.`, "error");
      console.error(e);
    } finally {
      flowBusy = false;
      syncSessionControls({ live: !!(session && session.connected) });
    }
  }

  async function stopSession() {
    if (!session || !session.connected) return;
    presentationPaused = false;
    flowBusy = false;
    syncSessionControls({ live: true, busy: true });
    try {
      await session.disconnect();
    } catch (e) {
      session = null;
      syncSessionControls();
      setStatus("error", "Error");
      setCaption(`Could not end the presentation: ${String(e?.message || e)}`);
      console.error(e);
      return;
    }

    if (session && !session.connected) {
      session = null;
      syncSessionControls();
      showEndScreen();
    }
  }

  async function startSession(withMic) {
    stage.querySelector(".overlay")?.remove();
    presentationPaused = false;
    flowBusy = false;
    syncSessionControls({ busy: true });
    setStatus("connecting", "Connecting…");
    setCaption(withMic ? "Requesting microphone…" : "Connecting (text-only)…");

    session = createVoiceSession({
      deckId,
      enableMic: withMic,
      onConnected: () => {
        syncSessionControls({ live: true });
      },
      onStatus: setStatus,
      onCaption: setCaption,
      onSlide: (n) => { renderSlide(n); showBreadcrumb("Moved here from your question"); },
      onDisconnected: () => {
        presentationPaused = false;
        flowBusy = false;
        session = null;
        syncSessionControls();
        showEndScreen();
      },
    });

    try {
      await session.connect();
      syncSessionControls({ live: true });
    } catch (e) {
      presentationPaused = false;
      flowBusy = false;
      session = null;
      syncSessionControls();
      const msg = String(e?.message || e);
      const micIssue = /permission|microphone|notallowed|denied|getusermedia/i.test(msg);
      if (withMic && micIssue) {
        setStatus("error", "Mic blocked");
        setCaption("Microphone blocked. You can still present with typed questions.");
        offerTextOnly();
      } else {
        setStatus("error", "Error");
        setCaption(`Could not connect: ${msg}`);
      }
      console.error(e);
    }
  }

  function offerTextOnly() {
    if (document.getElementById("text-only-btn")) return;
    const btn = h("button", { id: "text-only-btn", class: "btn ghost", onClick: () => { btn.remove(); startSession(false); } }, "Continue without mic (text only)");
    controls.append(btn);
  }

  function showEndScreen() {
    if (stage.querySelector(".overlay")) return;
    const overlay = h("div", { class: "overlay" },
      h("div", { class: "box" },
        h("h3", {}, "Thanks for watching"),
        h("p", {}, `That's the end of "${deck.title}". Want to run it again, or reach out to the creator?`),
        h("div", { style: "display:flex;gap:10px;justify-content:center;flex-wrap:wrap" },
          h("button", { class: "btn", onClick: () => { overlay.remove(); go(1); startSession(true); } }, "▶ Start again"),
          h("a", { class: "btn ghost", href: "mailto:?subject=About your presentation" }, "Contact / book a call"),
          h("a", { class: "btn ghost", href: "#/" }, "All decks"),
        ),
      ));
    stage.append(overlay);
  }

  // --- Keyboard nav ---
  function onKey(e) {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    if (e.key === "ArrowLeft") go(current - 1, true);
    else if (e.key === "ArrowRight") go(current + 1, true);
  }
  window.addEventListener("keydown", onKey);

  renderSlide(1);

  cleanup = () => {
    window.removeEventListener("keydown", onKey);
    if (session) session.disconnect();
  };
  return cleanup;
}
