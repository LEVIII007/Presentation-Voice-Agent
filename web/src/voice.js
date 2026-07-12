// RTVI voice session wrapper — connects to the backend's per-deck pipeline.
// Adapted from the original single-page demo; now parameterized by deck.

import { RTVIClient, RTVIEvent, RTVIMessage } from "@pipecat-ai/client-js";
import { WebSocketTransport } from "@pipecat-ai/websocket-transport";

import { BACKEND_URL } from "./api.js";
import { createPresentationFlowMessage, createRtviClientMessage } from "./rtvi-messages.js";

function parseServerMessage(message) {
  if (message && message.data && message.data.message_type) return message.data;
  if (message && message.message_type) return message;
  if (message && message.type === "server-message" && message.data) return message.data;
  return null;
}

function humanizeErrorMessage(message) {
  if (!message) return "Unknown error";
  if (/http 402/i.test(message)) {
    return "The configured speech provider rejected the connection with HTTP 402. Check that provider's billing or credits, or switch TTS providers in the server config.";
  }
  return message;
}

function formatRtviError(err) {
  const dataError = err && err.data && typeof err.data.error === "string" ? err.data.error : "";
  const message = dataError || (typeof err?.message === "string" ? err.message : "") || (typeof err === "string" ? err : "");
  if (message) return humanizeErrorMessage(message);
  try {
    return humanizeErrorMessage(JSON.stringify(err));
  } catch {
    return humanizeErrorMessage(String(err));
  }
}

// Keep only a short playback-aligned tail so the caption stays within a
// compact 2-3 line window instead of growing into a paragraph.
const CAPTION_TAIL_CHARS_DESKTOP = 220;
const CAPTION_TAIL_CHARS_MOBILE = 110;
const CAPTION_INITIAL_DELAY_MS = 120;
const CAPTION_MIN_DELAY_MS = 150;
const CAPTION_MAX_DELAY_MS = 410;
const CAPTION_BASE_DELAY_MS = 90;
const CAPTION_CHAR_DELAY_MS = 22;
const CAPTION_CLAUSE_PAUSE_MS = 45;
const CAPTION_SENTENCE_PAUSE_MS = 110;

function captionTailChars() {
  if (typeof window !== "undefined" && window.matchMedia("(max-width: 720px)").matches) {
    return CAPTION_TAIL_CHARS_MOBILE;
  }
  return CAPTION_TAIL_CHARS_DESKTOP;
}

function captionTailSegments(segments) {
  const tail = [];
  let chars = 0;
  const limit = captionTailChars();
  for (let i = segments.length - 1; i >= 0; i -= 1) {
    const segment = String(segments[i] || "").trim();
    if (!segment) continue;
    const nextChars = chars + segment.length + (tail.length ? 1 : 0);
    if (tail.length && nextChars > limit) break;
    tail.unshift(segment);
    chars = nextChars;
  }
  return tail;
}

function tokenizeCaptionText(text) {
  return String(text || "").trim().match(/\S+/g) || [];
}

function estimateCaptionDelay(segment, backlog) {
  const plain = String(segment || "").replace(/[^\p{L}\p{N}]/gu, "");
  const chars = Math.max(plain.length, 1);
  let ms = CAPTION_BASE_DELAY_MS + Math.min(chars, 10) * CAPTION_CHAR_DELAY_MS;
  if (/[,:;]$/.test(segment)) ms += CAPTION_CLAUSE_PAUSE_MS;
  if (/[.?!]$/.test(segment)) ms += CAPTION_SENTENCE_PAUSE_MS;
  if (backlog > 4) ms -= Math.min((backlog - 4) * 14, 80);
  return Math.max(CAPTION_MIN_DELAY_MS, Math.min(ms, CAPTION_MAX_DELAY_MS));
}

// States reported via onStatus: connecting | connected | speaking | listening | thinking | offline | error
export function createVoiceSession({ deckId, enableMic = true, onStatus, onCaption, onSlide, onConnected, onDisconnected, onQaLogged }) {
  let client = null;
  let connected = false;
  let terminalError = false;
  let spokenSegments = [];
  let revealedSegmentCount = 0;
  let captionRevealTimer = null;

  function stopCaptionReveal() {
    if (captionRevealTimer) {
      clearTimeout(captionRevealTimer);
      captionRevealTimer = null;
    }
  }

  function renderRevealedCaption({ final = false } = {}) {
    const visibleSegments = final
      ? spokenSegments
      : spokenSegments.slice(0, revealedSegmentCount);
    if (!visibleSegments.length) return;
    const tail = captionTailSegments(visibleSegments);
    onCaption({
      speaker: "Presenter",
      segments: tail,
      activeIndex: final ? -1 : tail.length - 1,
    });
  }

  function scheduleCaptionReveal(delay = CAPTION_INITIAL_DELAY_MS) {
    if (captionRevealTimer || revealedSegmentCount >= spokenSegments.length) return;
    captionRevealTimer = setTimeout(() => {
      captionRevealTimer = null;
      if (revealedSegmentCount >= spokenSegments.length) return;
      revealedSegmentCount += 1;
      renderRevealedCaption();
      const backlog = spokenSegments.length - revealedSegmentCount;
      if (backlog > 0) {
        scheduleCaptionReveal(
          estimateCaptionDelay(spokenSegments[revealedSegmentCount - 1], backlog),
        );
      }
    }, delay);
  }

  function resetPresenterCaption() {
    stopCaptionReveal();
    spokenSegments = [];
    revealedSegmentCount = 0;
  }

  function sendTextMessage(content, options = { run_immediately: true, audio_response: true }) {
    if (!client || !connected) return false;
    client.sendMessage(new RTVIMessage("send-text", { content, options }));
    return true;
  }

  async function connect() {
    onStatus("connecting", "Connecting…");
    const transport = new WebSocketTransport();
    client = new RTVIClient({
      transport,
      params: {
        baseUrl: BACKEND_URL,
        endpoints: { connect: `/connect?deck_id=${encodeURIComponent(deckId)}` },
      },
      enableMic,
      enableCam: false,
      callbacks: {
        onConnected: () => {
          terminalError = false;
          connected = true;
          onConnected && onConnected();
          onStatus("connected", "Listening");
          onCaption("Listening — ask a question any time, or just start talking to interrupt.");
        },
        onDisconnected: () => {
          connected = false;
          resetPresenterCaption();
          if (!terminalError) onStatus("offline", "Session ended");
          onDisconnected && onDisconnected();
        },
        onServerMessage: (message) => {
          const data = parseServerMessage(message);
          if (!data) return;
          if (data.message_type === "go_to_slide" && typeof data.slide === "number") {
            onSlide(data.slide);
          } else if (data.message_type === "qa_logged" && data.entry) {
            onQaLogged && onQaLogged(data.entry);
          } else if (data.message_type === "session_error" && data.message) {
            terminalError = true;
            onStatus("error", "Speech unavailable");
            onCaption(data.message);
          }
        },
        onError: (err) => {
          const msg = formatRtviError(err);
          terminalError = true;
          onStatus("error", "Error");
          onCaption(`Error: ${msg}`);
          console.error("RTVI error:", err);
        },
      },
    });

    client.on(RTVIEvent.BotStartedSpeaking, () => {
      if (connected) onStatus("speaking", "Presenter speaking");
    });
    client.on(RTVIEvent.BotStoppedSpeaking, () => {
      if (spokenSegments.length) {
        stopCaptionReveal();
        revealedSegmentCount = spokenSegments.length;
        renderRevealedCaption({ final: true });
      }
      if (connected) onStatus("connected", "Listening");
    });
    client.on(RTVIEvent.UserStartedSpeaking, () => {
      if (connected) onStatus("listening", "You're speaking");
    });
    client.on(RTVIEvent.UserTranscript, (data) => {
      if (data && data.text && data.final) {
        onCaption({ speaker: "You", text: data.text });
        // gpt-5-mini is a reasoning model — there's a beat before the answer.
        if (connected) onStatus("thinking", "Thinking…");
      }
    });
    // Captions must track the *audio*, not the LLM stream. BotTranscript fires
    // per sentence as the LLM generates — seconds ahead of the voice on a long
    // narration, so the caption would race to the last sentence while the
    // middle is still being spoken. BotTtsText is much closer to playback, but
    // in practice it can still lead the browser audio by a small beat, so we
    // reveal those incoming words through a short local pacing queue.
    client.on(RTVIEvent.BotLlmStarted, () => {
      resetPresenterCaption();
    });
    client.on(RTVIEvent.BotTtsText, (data) => {
      const segments = tokenizeCaptionText(data?.text);
      if (!segments.length) return;
      spokenSegments.push(...segments);
      scheduleCaptionReveal();
    });

    await client.connect();
  }

  async function disconnect() {
    resetPresenterCaption();
    if (client) {
      try {
        await client.disconnect();
      } catch (e) {
        console.error(e);
      }
      client = null;
    }
    connected = false;
  }

  // Manual slide jump -> silently tell the agent so its mental model of "the
  // current slide" stays in sync, without making it speak.
  function notifyManualSlide(n, title) {
    sendTextMessage(
      `(System note, not spoken aloud: the audience is now looking at slide ${n}${title ? `, titled "${title}"` : ""}. Treat that slide as the visual context for follow-up questions from here unless you choose to go_to_slide elsewhere. Use that slide number if you later need to inspect its image. Do not announce this note.)`,
      { run_immediately: false, audio_response: false },
    );
    // Also sync the server's own notion of the current slide, so transcript
    // slide tags and image lookups stay truthful (the note above only informs
    // the model).
    if (client && connected) {
      client.sendMessage(createRtviClientMessage("manual-slide", { slide: n }));
    }
  }

  // Pause / resume go straight to the live session as a control command, not
  // through the LLM: pause must cut the current speech immediately (mid-sentence),
  // which a text prompt the model has to notice and act on can't guarantee. The
  // backend interrupts the pipeline and, on resume, replays from the start of the
  // interrupted sentence.
  function setPresentationFlow(action) {
    if (action !== "pause" && action !== "resume") return false;
    if (!client || !connected) return false;
    client.sendMessage(createPresentationFlowMessage(action));
    return true;
  }

  return {
    connect,
    disconnect,
    notifyManualSlide,
    setPresentationFlow,
    get connected() {
      return connected;
    },
  };
}
