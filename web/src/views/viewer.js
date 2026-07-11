// The viewer — the live talkable deck. This is the shareable, mobile-friendly
// screen. Renders each slide (image if ingested, else title+bullets), a slide
// rail for manual nav, live captions, a typed-question fallback, mic priming,
// thinking indicator, and an end screen with the creator CTA.

import { api, escapeHtml } from "../api.js";
import { h, mount, toast } from "../dom.js";
import { createVoiceSession } from "../voice.js";

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

const ICONS = {
  captions: "<svg viewBox='0 0 24 24'><path d='M11 6 7.5 9H5v6h2.5L11 18z'/><path d='M14.5 8.5a4.5 4.5 0 0 1 0 7'/><path d='M16.5 6a8 8 0 0 1 0 12'/></svg>",
  pause: "<svg viewBox='0 0 24 24'><path d='M8 6h3v12H8z' fill='currentColor' stroke='none'/><path d='M13 6h3v12h-3z' fill='currentColor' stroke='none'/></svg>",
  play: "<svg viewBox='0 0 24 24'><path d='M9 7v10l8-5z' fill='currentColor' stroke='none'/></svg>",
  stop: "<svg viewBox='0 0 24 24'><rect x='7' y='7' width='10' height='10' rx='2' fill='currentColor' stroke='none'/></svg>",
};

function iconEl(name) {
  return h("span", { class: "control-icon", "aria-hidden": "true", html: ICONS[name] });
}

function controlNameEl(label) {
  return h("span", { class: "control-name" }, label);
}

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
  let latestCaption = "Press Start and allow the microphone to begin — or type your question below.";
  let presentationPaused = false;
  let flowBusy = false;
  let session = null;
  let cleanup = () => {};
  let statusState = "offline";
  let statusLabel = STATUS_TEXT.offline;
  // Q&A history: the backend pairs each genuine audience question with its
  // answer (filtering greetings, commands, and garble) and pushes a cleaned
  // entry; we just render them here, newest first.
  const qaLog = []; // { question, answer, askedSlide, answeredSlide }

  // --- Elements ---
  const rail = h("div", { class: "rail" });
  const stageInner = h("div", { class: "stage-inner" });
  const slideCard = h("div", { class: "slide-card" });
  stageInner.append(slideCard);
  const breadcrumb = h("div", { class: "nav-breadcrumb" }, "");
  const prevBtn = h("button", { class: "stage-nav prev", title: "Previous slide", onClick: () => go(current - 1, true) }, "‹");
  const nextBtn = h("button", { class: "stage-nav next", title: "Next slide", onClick: () => go(current + 1, true) }, "›");
  const flowIcon = iconEl("pause");
  const flowName = controlNameEl("Pause");
  const stageFlowBtn = h("button", {
    class: "session-icon-btn stage-flow",
    type: "button",
    title: "Pause automatic flow",
    "aria-label": "Pause automatic flow",
    onClick: togglePresentationFlow,
  }, flowIcon, flowName);
  stageFlowBtn.hidden = true;
  const stage = h("div", { class: "stage" }, prevBtn, breadcrumb, stageInner, nextBtn);

  const statusPill = h("div", { class: "control-status offline" },
    h("span", { class: "status-indicator-dot", "aria-hidden": "true" }),
    h("span", { id: "st-text", class: "status-label" }, "Offline"));
  const caption = h("div", { class: "caption" }, latestCaption);
  const textInput = h("input", { type: "text", placeholder: "Type a question…" });
  const textForm = h("form", { class: "textbar", onSubmit: (e) => { e.preventDefault(); const v = textInput.value.trim(); if (v) { ask(v); textInput.value = ""; } } },
    textInput, h("button", { class: "btn sm", type: "submit" }, "Send"));

  const startIcon = iconEl("play");
  const startName = controlNameEl("Start");
  const startBtn = h("button", {
    class: "session-icon-btn start-control",
    type: "button",
    title: "Start presentation",
    "aria-label": "Start presentation",
    onClick: toggleConnect,
  }, startIcon, startName);
  const capIcon = iconEl("captions");
  const capName = controlNameEl("Captions");
  const capToggle = h("button", {
    class: "session-icon-btn toggle-cap active",
    type: "button",
    title: "Turn captions off",
    "aria-label": "Turn captions off",
    onClick: toggleCaptions,
  }, capIcon, capName);
  const controls = h("div", { class: "control-panel" }, capToggle, stageFlowBtn, startBtn, statusPill);
  const extraActions = h("div", { class: "dock-extra" });
  const dock = h("div", { class: "dock" },
    caption, textForm, controls, extraActions,
    h("div", { class: "hint", style: "font-size:12px;color:var(--faint)" }, "Tip: just start talking to interrupt the presenter. Use headphones to avoid echo."),
  );
  syncCaptionsControl();

  // --- Q&A history panel (right side; a drawer on mobile) ---
  const qaList = h("div", { class: "qa-list" });
  const qaCount = h("span", { class: "qa-count" }, "0");
  const qaPanel = h("aside", { class: "qa-panel" },
    h("div", { class: "qa-head" },
      h("span", { class: "qa-title" }, "Your questions ", qaCount),
      h("button", { class: "qa-x", type: "button", title: "Hide", onClick: closeQa }, "×")),
    qaList);
  const qaBackdrop = h("div", { class: "qa-backdrop", onClick: closeQa });
  const qaFabCount = h("span", { class: "qa-fab-count" }, "0");
  const qaFab = h("button", { class: "qa-fab", type: "button", title: "Your questions", onClick: openQa },
    "Questions", qaFabCount);

  const viewer = h("div", { class: "viewer" }, rail, stage, qaPanel, dock, qaBackdrop, qaFab);
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

  // --- Q&A history ---
  function openQa() { viewer.classList.add("qa-open"); }
  function closeQa() { viewer.classList.remove("qa-open"); }

  function updateQaCount() {
    const n = String(qaLog.length);
    qaCount.textContent = n;
    qaFabCount.textContent = n;
    qaFab.classList.toggle("has", qaLog.length > 0);
  }

  function renderQaLog() {
    if (qaLog.length === 0) {
      qaList.replaceChildren(h("div", { class: "qa-empty" },
        "Questions you ask during the talk show up here — with the answer and the slide you were on. Tap one to jump back to it."));
      return;
    }
    qaList.replaceChildren(...qaLog.map((e) => {
      const badge = e.answeredSlide !== e.askedSlide
        ? `Slide ${e.askedSlide} → ${e.answeredSlide}`
        : `Slide ${e.askedSlide}`;
      return h("button", { class: "qa-item", type: "button", title: `Jump to slide ${e.answeredSlide}`, onClick: () => go(e.answeredSlide, true) },
        h("div", { class: "qa-item-top" },
          h("span", { class: "qa-q" }, e.question),
          h("span", { class: "qa-slide" }, badge)),
        h("div", { class: "qa-a" }, e.answer));
    }));
  }

  // A cleaned Q&A entry pushed by the backend: { question, answer, askedSlide,
  // answeredSlide }. Pairing and filtering already happened server-side.
  function recordQuestion(entry) {
    if (!entry || !entry.question) return;
    qaLog.unshift({
      question: entry.question,
      answer: entry.answer || "",
      askedSlide: entry.askedSlide,
      answeredSlide: entry.answeredSlide ?? entry.askedSlide,
    });
    updateQaCount();
    renderQaLog();
  }

  function renderStatus() {
    let state = statusState;
    let text = statusLabel || STATUS_TEXT[state] || "";
    if (presentationPaused && state === "connected") {
      state = "paused";
      text = STATUS_TEXT.paused;
    }
    statusPill.className = `control-status ${state}${["speaking", "listening", "thinking", "connected"].includes(state) ? " live" : ""}`;
    document.getElementById("st-text").textContent = text;
  }

  function setStatus(state, text) {
    statusState = state;
    statusLabel = text || STATUS_TEXT[state] || "";
    renderStatus();
  }

  function setCaption(html) {
    latestCaption = html;
    if (captionsOn) caption.innerHTML = html;
  }

  function syncCaptionsControl() {
    capToggle.classList.toggle("active", captionsOn);
    const label = captionsOn ? "Turn captions off" : "Turn captions on";
    capToggle.title = label;
    capToggle.setAttribute("aria-label", label);
  }

  function toggleCaptions() {
    captionsOn = !captionsOn;
    syncCaptionsControl();
    caption.innerHTML = captionsOn ? latestCaption : "";
  }

  function syncSessionControls({ live = false, busy = false } = {}) {
    startBtn.disabled = busy;
    startBtn.classList.toggle("live", live);
    startBtn.title = live ? "Stop presentation" : "Start presentation";
    startBtn.setAttribute("aria-label", live ? "Stop presentation" : "Start presentation");
    startIcon.innerHTML = live ? ICONS.stop : ICONS.play;
    startName.textContent = live ? "Stop" : "Start";
    stageFlowBtn.hidden = !live;
    stageFlowBtn.disabled = busy || flowBusy;
    stageFlowBtn.title = presentationPaused ? "Resume automatic flow" : "Pause automatic flow";
    stageFlowBtn.setAttribute("aria-label", stageFlowBtn.title);
    flowIcon.innerHTML = presentationPaused ? ICONS.play : ICONS.pause;
    flowName.textContent = presentationPaused ? "Resume" : "Pause";
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
      onQaLogged: recordQuestion,
      onDisconnected: () => {
        presentationPaused = false;
        flowBusy = false;
        session = null;
        syncSessionControls();
        if (statusState !== "error") showEndScreen();
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
    const btn = h("button", { id: "text-only-btn", class: "btn ghost text-only-btn", type: "button", onClick: () => { btn.remove(); startSession(false); } }, "Continue without mic");
    extraActions.append(btn);
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
  updateQaCount();
  renderQaLog();
  // Open by default where there's room; on phones it stays a closed drawer so
  // it never covers the slide until the viewer taps the Questions button.
  if (!window.matchMedia("(max-width: 720px)").matches) openQa();

  cleanup = () => {
    window.removeEventListener("keydown", onKey);
    if (session) session.disconnect();
  };
  return cleanup;
}
