// The viewer — the live talkable deck. This is the shareable, mobile-friendly
// screen. Renders each slide (image if ingested, else title+bullets), a slide
// rail for manual nav, live captions, mic priming, thinking indicator, and an
// end screen with the creator CTA.

import { api } from "../api.js";
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

const STATUS_BADGE = {
  connecting: "Connecting",
  connected: "Ready",
  paused: "Paused",
  speaking: "Presenter",
  listening: "You",
  thinking: "Thinking",
  offline: "Offline",
  error: "Error",
};

const STATUS_WAVE_BARS = [0.24, 0.36, 0.54, 0.74, 0.92, 1, 0.86, 0.64, 0.42, 0.22, 0.18, 0.26, 0.44, 0.66, 0.88, 1, 0.9, 0.72, 0.52];

const ICONS = {
  captions: "<svg viewBox='0 0 24 24'><path d='M11 6 7.5 9H5v6h2.5L11 18z'/><path d='M14.5 8.5a4.5 4.5 0 0 1 0 7'/><path d='M16.5 6a8 8 0 0 1 0 12'/></svg>",
  pause: "<svg viewBox='0 0 24 24'><path d='M8 6h3v12H8z' fill='currentColor' stroke='none'/><path d='M13 6h3v12h-3z' fill='currentColor' stroke='none'/></svg>",
  play: "<svg viewBox='0 0 24 24'><path d='M9 7v10l8-5z' fill='currentColor' stroke='none'/></svg>",
  stop: "<svg viewBox='0 0 24 24'><rect x='7' y='7' width='10' height='10' rx='2' fill='currentColor' stroke='none'/></svg>",
  fullscreen: "<svg viewBox='0 0 24 24'><path d='M8 4H4v4'/><path d='M16 4h4v4'/><path d='M20 16v4h-4'/><path d='M4 16v4h4'/><path d='M9 4 4 9'/><path d='m15 4 5 5'/><path d='m20 15-5 5'/><path d='m4 15 5 5'/></svg>",
  fullscreenExit: "<svg viewBox='0 0 24 24'><path d='M10 4H4v6'/><path d='M14 4h6v6'/><path d='M20 14v6h-6'/><path d='M4 14v6h6'/><path d='m4 10 6-6'/><path d='m20 10-6-6'/><path d='m20 14-6 6'/><path d='m4 14 6 6'/></svg>",
};

function iconEl(name) {
  return h("span", { class: "control-icon", "aria-hidden": "true", html: ICONS[name] });
}

function controlNameEl(label) {
  return h("span", { class: "control-name" }, label);
}

function statusWaveEl() {
  return h(
    "div",
    { class: "status-wave", "aria-hidden": "true" },
    STATUS_WAVE_BARS.map((amp, index) => h("span", {
      class: "status-wave-bar",
      style: `--bar:${amp};--i:${index};`,
    })),
  );
}

const NO_SPACE_BEFORE_SEGMENT_RE = /^[,.;:!?%)\]}]/;
const NO_SPACE_AFTER_SEGMENT_RE = /[(\[{'"“]$/;

function needsSpaceBeforeSegment(previous, current) {
  if (!previous || !current) return false;
  if (NO_SPACE_BEFORE_SEGMENT_RE.test(current) || /^[’']/.test(current)) return false;
  if (NO_SPACE_AFTER_SEGMENT_RE.test(previous)) return false;
  return true;
}

function renderCaptionContent(content) {
  const normalized = typeof content === "string" ? { text: content } : (content || {});
  const line = h("div", { class: "caption-line" });
  if (normalized.speaker) {
    line.append(h("span", {
      class: `caption-speaker ${normalized.speaker === "Presenter" ? "presenter" : "audience"}`,
    }, `${normalized.speaker}:`));
  }
  if (Array.isArray(normalized.segments)) {
    const words = h("span", { class: "caption-words" });
    normalized.segments.forEach((segment, index) => {
      if (needsSpaceBeforeSegment(normalized.segments[index - 1], segment)) words.append(" ");
      words.append(h("span", {
        class: `caption-word${index === normalized.activeIndex ? " active" : ""}`,
      }, segment));
    });
    line.append(words);
    return line;
  }
  line.append(h("span", { class: "caption-text" }, normalized.text || ""));
  return line;
}

export async function renderViewer(deckId) {
  mount(
    h(
      "div",
      { class: "page viewer-loading", dataset: { surface: "viewer", subtitle: "Live presentation", chrome: "none" } },
      h("div", { class: "spinner" }),
    ),
  );

  let deck;
  try {
    deck = await api.getDeck(deckId);
  } catch (e) {
    mount(
      h(
        "div",
        { class: "page", dataset: { surface: "viewer", subtitle: "Live presentation", chrome: "none" } },
        h("div", { class: "banner error" }, `Could not load deck: ${e.message}`),
        h("a", { href: "#/" }, "← All decks"),
      ),
    );
    return;
  }
  if (deck.status !== "ready") {
    mount(
      h(
        "div",
        { class: "page", dataset: { surface: "viewer", subtitle: "Live presentation", chrome: "none" } },
        h("div", { class: "banner info" }, "This deck isn't ready to present yet."),
        h("a", { class: "btn", href: `#/deck/${deckId}/processing` }, "See progress"),
      ),
    );
    return;
  }

  const slides = deck.slides;
  const total = slides.length;
  let current = 1;
  let captionsOn = true;
  let latestCaption = "Press Start and allow the microphone to begin, then just speak to interrupt or ask a question.";
  let presentationPaused = false;
  let flowBusy = false;
  let slideFocusMode = false;
  let restoreQaAfterFocus = false;
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
  const focusStageIcon = iconEl("fullscreenExit");
  const focusStageBtn = h("button", {
    class: "stage-focus-toggle",
    type: "button",
    title: "Exit slide focus",
    "aria-label": "Exit slide focus",
    onClick: () => toggleSlideFocus(false),
  }, focusStageIcon);
  focusStageBtn.hidden = true;
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
  const stage = h("div", { class: "stage" }, h("div", { class: "stage-aura", "aria-hidden": "true" }), prevBtn, breadcrumb, stageInner, nextBtn, focusStageBtn);

  const statusText = h("span", { id: "st-text", class: "status-label" }, STATUS_BADGE.offline);
  const statusPill = h("div", {
    class: "control-status offline",
    role: "status",
    "aria-live": "polite",
    "aria-label": STATUS_TEXT.offline,
    title: STATUS_TEXT.offline,
  },
  statusWaveEl(),
  h("div", { class: "status-copy" },
    h("span", { class: "status-indicator-dot", "aria-hidden": "true" }),
    statusText));
  const caption = h("div", { class: "caption" }, renderCaptionContent(latestCaption));
  const metaDeck = h("div", { class: "viewer-meta-title" }, deck.title || "Presentation");
  const metaCount = h("div", { class: "viewer-meta-count" }, `1 / ${total}`);

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
  const fullscreenIcon = iconEl("fullscreen");
  const fullscreenName = controlNameEl("Expand");
  const fullscreenBtn = h("button", {
    class: "session-icon-btn fullscreen-toggle",
    type: "button",
    title: "Show slide full screen",
    "aria-label": "Show slide full screen",
    onClick: () => toggleSlideFocus(),
  }, fullscreenIcon, fullscreenName);
  const controls = h("div", { class: "control-panel" }, capToggle, stageFlowBtn, startBtn, fullscreenBtn, statusPill);
  const hint = h("div", { class: "hint" }, "Tip: just start talking to interrupt the presenter. Use headphones to avoid echo.");
  const dock = h("div", { class: "dock" },
    h(
      "div",
      { class: "viewer-meta" },
      h(
        "div",
        { class: "viewer-meta-copy" },
        metaDeck,
      ),
      controls,
      metaCount,
    ),
    caption,
    hint,
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
  const railHome = h(
    "a",
    { class: "rail-home", href: "#/", title: "Back to all decks" },
    h("span", { class: "rail-home-label" }, "Home Nav"),
  );
  rail.append(railHome);
  const mobileHome = h("a", { class: "viewer-home-mobile", href: "#/" }, "Home Nav");

  const viewer = h("div", {
    class: "viewer",
    dataset: { surface: "viewer", subtitle: deck.title || "Live presentation", chrome: "none" },
  }, rail, stage, qaPanel, dock, qaBackdrop, qaFab, mobileHome);
  mount(viewer);

  function syncSlideFocusControl() {
    viewer.classList.toggle("slide-focus", slideFocusMode);
    fullscreenBtn.classList.toggle("active", slideFocusMode);
    fullscreenBtn.title = slideFocusMode ? "Exit slide focus" : "Show slide full screen";
    fullscreenBtn.setAttribute("aria-label", fullscreenBtn.title);
    fullscreenIcon.innerHTML = slideFocusMode ? ICONS.fullscreenExit : ICONS.fullscreen;
    fullscreenName.textContent = slideFocusMode ? "Exit" : "Focus";
    focusStageBtn.hidden = !slideFocusMode;
  }

  function toggleSlideFocus(force) {
    const next = typeof force === "boolean" ? force : !slideFocusMode;
    if (next === slideFocusMode) return;
    slideFocusMode = next;
    if (slideFocusMode) {
      restoreQaAfterFocus = viewer.classList.contains("qa-open");
      closeQa();
    } else if (restoreQaAfterFocus && !window.matchMedia("(max-width: 720px)").matches) {
      openQa();
      restoreQaAfterFocus = false;
    } else {
      restoreQaAfterFocus = false;
    }
    syncSlideFocusControl();
  }

  syncSlideFocusControl();

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
    metaCount.textContent = `${n} / ${total}`;
    [...rail.children].forEach((c) => c.classList.toggle("active", Number(c.dataset.n) === n));
    const active = rail.querySelector(`[data-n="${n}"]`);
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
    statusPill.className = `control-status ${state}${["connecting", "speaking", "listening", "thinking", "connected"].includes(state) ? " live" : ""}`;
    statusPill.setAttribute("aria-label", text);
    statusPill.title = text;
    statusText.textContent = STATUS_BADGE[state] || text;
  }

  function setStatus(state, text) {
    statusState = state;
    statusLabel = text || STATUS_TEXT[state] || "";
    renderStatus();
  }

  function setCaption(content) {
    latestCaption = content;
    renderCaption();
  }

  function renderCaption() {
    caption.replaceChildren();
    if (!captionsOn) return;
    caption.append(renderCaptionContent(latestCaption));
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
    renderCaption();
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
    if (hint) {
      hint.textContent = presentationPaused
        ? "Tip: the microphone is muted while paused, so side conversations won't wake the presenter."
        : "Tip: just start talking to interrupt the presenter. Use headphones to avoid echo.";
    }
  }

  // --- Connect / disconnect ---
  async function toggleConnect() {
    if (session && session.connected) {
      await stopSession();
      return;
    }
    await startSession();
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
      const micUpdated = session.setMicEnabled(!nextPaused);
      if (!micUpdated) throw new Error(`Could not ${action === "pause" ? "mute" : "re-enable"} the microphone.`);
      presentationPaused = nextPaused;
      renderStatus();
      setCaption(
        presentationPaused
          ? "Presentation paused. The microphone is muted until you resume."
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

  async function startSession() {
    stage.querySelector(".overlay")?.remove();
    presentationPaused = false;
    flowBusy = false;
    syncSessionControls({ busy: true });
    setStatus("connecting", "Connecting…");
    setCaption("Requesting microphone…");

    session = createVoiceSession({
      deckId,
      enableMic: true,
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
      if (micIssue) {
        setStatus("error", "Mic blocked");
        setCaption("Microphone access is required to ask questions and interrupt the presenter. Allow the mic and press Start again.");
        toast("Microphone access is required for live Q&A.", "error");
      } else {
        setStatus("error", "Error");
        setCaption(`Could not connect: ${msg}`);
      }
      console.error(e);
    }
  }

  function showEndScreen() {
    if (stage.querySelector(".overlay")) return;
    const overlay = h("div", { class: "overlay" },
      h("div", { class: "box" },
        h("h3", {}, "Thanks for watching"),
        h("p", {}, `That's the end of "${deck.title}". Want to run it again, or reach out to the creator?`),
        h("div", { style: "display:flex;gap:10px;justify-content:center;flex-wrap:wrap" },
          h("button", { class: "btn", onClick: () => { overlay.remove(); go(1); startSession(); } }, "▶ Start again"),
          h("a", { class: "btn ghost", href: "#/" }, "All decks"),
        ),
      ));
    stage.append(overlay);
  }

  // --- Keyboard nav ---
  function onKey(e) {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    if (e.key === "Escape" && slideFocusMode) {
      e.preventDefault();
      toggleSlideFocus(false);
    } else if (e.key === "ArrowLeft") go(current - 1, true);
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
