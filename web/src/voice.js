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
// compact 2 line window instead of growing into a paragraph.
const CAPTION_TAIL_CHARS_DESKTOP = 150;
const CAPTION_TAIL_CHARS_MOBILE = 84;
// Below this many milliseconds of scheduled delay, just show the word now —
// not worth a timer for a delay indistinguishable from "immediately".
const CAPTION_MIN_SCHEDULE_MS = 50;

function captionTailChars() {
  if (typeof window !== "undefined" && window.matchMedia("(max-width: 720px)").matches) {
    return CAPTION_TAIL_CHARS_MOBILE;
  }
  return CAPTION_TAIL_CHARS_DESKTOP;
}

function wordTailSegments(segments, limit) {
  const tail = [];
  let chars = 0;
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

function captionBoundaryRank(segment) {
  if (/[.!?]["')\]]*$/.test(segment)) return 2;
  if (/[,;:]["')\]]*$/.test(segment)) return 1;
  return 0;
}

function countCaptionChars(segments) {
  let chars = 0;
  for (const raw of segments) {
    const segment = String(raw || "").trim();
    if (!segment) continue;
    chars += segment.length + (chars ? 1 : 0);
  }
  return chars;
}

function captionTailSegments(segments) {
  const normalized = segments.map((segment) => String(segment || "").trim()).filter(Boolean);
  const limit = captionTailChars();
  if (!normalized.length) return [];

  const chunks = [];
  let currentChunk = [];
  for (const segment of normalized) {
    currentChunk.push(segment);
    if (captionBoundaryRank(segment)) {
      chunks.push(currentChunk);
      currentChunk = [];
    }
  }
  if (currentChunk.length) chunks.push(currentChunk);

  const selected = [];
  let chars = 0;
  for (let i = chunks.length - 1; i >= 0; i -= 1) {
    const chunk = chunks[i];
    const chunkChars = countCaptionChars(chunk);
    const nextChars = chars + chunkChars + (selected.length ? 1 : 0);
    if (selected.length && nextChars > limit) break;
    selected.unshift(...chunk);
    chars = nextChars;
  }

  return chars > limit ? wordTailSegments(selected, limit) : selected;
}

function tokenizeCaptionText(text) {
  return String(text || "").trim().match(/\S+/g) || [];
}

// States reported via onStatus: connecting | connected | speaking | listening | thinking | offline | error
export function createVoiceSession({ deckId, enableMic = true, onStatus, onCaption, onSlide, onConnected, onDisconnected, onQaLogged }) {
  let client = null;
  let connected = false;
  let terminalError = false;

  // --- Caption sync ----------------------------------------------------------
  // The server (_CaptionSyncProcessor) sends one "caption_word" message per
  // spoken word, tagged with an utterance_id (one per TTS sentence) and a
  // pts_offset: seconds from the start of *that sentence's own audio* — a
  // ground-truth number recovered from the TTS word-boundary timestamps, not a
  // wall-clock read, so it doesn't care how fast synthesis ran relative to
  // playback.
  //
  // The browser turns that into an absolute reveal time with one clock per
  // utterance: if the bot is already speaking, this sentence's audio queues
  // straight after the previous one, so "now" (performance.now()) is its
  // start; otherwise it's the first sentence of a fresh turn, so we wait for
  // BotStartedSpeaking (audio truly begins) and anchor then. Each word is then
  // shown via a plain setTimeout(pts_offset - elapsed) — one number, one
  // clock, no continuous playback-position inference required.
  let spokenSegments = []; // words already revealed, in order
  let botIsSpeaking = false;
  let currentUtteranceId = null;
  let utteranceAnchored = false;
  let utteranceAnchorTime = 0;
  let pendingUtteranceWords = []; // words buffered until this utterance is anchored
  let revealTimers = [];

  function clearRevealTimers() {
    for (const timer of revealTimers) clearTimeout(timer);
    revealTimers = [];
  }

  function renderRevealedCaption() {
    if (!spokenSegments.length) return;
    const tail = captionTailSegments(spokenSegments);
    onCaption({ speaker: "Presenter", segments: tail, activeIndex: tail.length - 1 });
  }

  function displayCaptionWord(word) {
    spokenSegments.push(word);
    renderRevealedCaption();
  }

  function scheduleCaptionWord(word, ptsOffset) {
    const elapsedSecs = (performance.now() - utteranceAnchorTime) / 1000;
    const delayMs = (ptsOffset - elapsedSecs) * 1000;
    if (delayMs <= CAPTION_MIN_SCHEDULE_MS) {
      displayCaptionWord(word);
    } else {
      revealTimers.push(setTimeout(() => displayCaptionWord(word), delayMs));
    }
  }

  // Anchors the in-flight utterance to "now" and releases anything buffered
  // while we waited for the bot to actually start speaking.
  function anchorUtterance() {
    utteranceAnchored = true;
    utteranceAnchorTime = performance.now();
    const words = pendingUtteranceWords;
    pendingUtteranceWords = [];
    for (const { word, ptsOffset } of words) scheduleCaptionWord(word, ptsOffset);
  }

  function handleCaptionWord(data) {
    const segments = tokenizeCaptionText(data.text);
    if (!segments.length) return;
    const ptsOffset = typeof data.pts_offset === "number" ? data.pts_offset : 0;
    if (data.utterance_id !== currentUtteranceId) {
      currentUtteranceId = data.utterance_id;
      utteranceAnchored = false;
      pendingUtteranceWords = [];
      if (botIsSpeaking) anchorUtterance();
    }
    for (const word of segments) {
      if (utteranceAnchored) {
        scheduleCaptionWord(word, ptsOffset);
      } else {
        pendingUtteranceWords.push({ word, ptsOffset });
      }
    }
  }

  function resetPresenterCaption() {
    clearRevealTimers();
    spokenSegments = [];
    pendingUtteranceWords = [];
    currentUtteranceId = null;
    utteranceAnchored = false;
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
          } else if (data.message_type === "caption_word" && typeof data.text === "string") {
            handleCaptionWord(data);
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
      botIsSpeaking = true;
      if (!utteranceAnchored) anchorUtterance();
      if (connected) onStatus("speaking", "Presenter speaking");
    });
    client.on(RTVIEvent.BotStoppedSpeaking, () => {
      botIsSpeaking = false;
      if (connected) onStatus("connected", "Listening");
    });
    client.on(RTVIEvent.UserStartedSpeaking, () => {
      // A barge-in cuts the bot's audio immediately server-side; drop any
      // caption words still scheduled so a stale one can't flash up after.
      clearRevealTimers();
      pendingUtteranceWords = [];
      utteranceAnchored = false;
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
    // narration. caption_word (see onServerMessage/handleCaptionWord) carries
    // a pts_offset computed from real TTS word-boundary timestamps, scheduled
    // against the utterance's own anchor — not the arrival time of the message.
    client.on(RTVIEvent.BotLlmStarted, () => {
      resetPresenterCaption();
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

  function setMicEnabled(enabled) {
    if (!client || !connected) return false;
    client.enableMic(Boolean(enabled));
    return true;
  }

  return {
    connect,
    disconnect,
    notifyManualSlide,
    setPresentationFlow,
    setMicEnabled,
    get connected() {
      return connected;
    },
  };
}
