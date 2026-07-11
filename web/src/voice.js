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

// Keep the caption to roughly its last two lines: once the utterance outgrows
// the budget, trim to the tail on a word boundary.
const CAPTION_TAIL_CHARS = 160;
function captionTail(text) {
  if (text.length <= CAPTION_TAIL_CHARS) return text;
  const tail = text.slice(-CAPTION_TAIL_CHARS);
  return "…" + tail.slice(tail.indexOf(" ") + 1);
}

// States reported via onStatus: connecting | connected | speaking | listening | thinking | offline | error
export function createVoiceSession({ deckId, enableMic = true, onStatus, onCaption, onSlide, onConnected, onDisconnected, onQaLogged }) {
  let client = null;
  let connected = false;
  let terminalError = false;

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
      if (connected) onStatus("connected", "Listening");
    });
    client.on(RTVIEvent.UserStartedSpeaking, () => {
      if (connected) onStatus("listening", "You're speaking");
    });
    client.on(RTVIEvent.UserTranscript, (data) => {
      if (data && data.text && data.final) {
        onCaption(`<b>You:</b> ${data.text}`);
        // gpt-5-mini is a reasoning model — there's a beat before the answer.
        if (connected) onStatus("thinking", "Thinking…");
      }
    });
    // Captions must track the *audio*, not the LLM stream. BotTranscript fires
    // per sentence as the LLM generates — seconds ahead of the voice on a long
    // narration, so the caption would race to the last sentence while the
    // middle is still being spoken. BotTtsText words are released by the
    // transport in sync with playback, so accumulate those instead.
    let spoken = "";
    client.on(RTVIEvent.BotLlmStarted, () => {
      spoken = "";
    });
    client.on(RTVIEvent.BotTtsText, (data) => {
      if (data && data.text) {
        spoken += (spoken ? " " : "") + data.text;
        onCaption(`<b>Presenter:</b> ${captionTail(spoken)}`);
      }
    });

    await client.connect();
  }

  async function disconnect() {
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

  // Typed question / starter chip -> same tool-calling LLM as voice, via the
  // RTVI first-class "send-text" handler. Speaks the answer and can call
  // go_to_slide, exactly like a spoken question.
  function sendText(text) {
    sendTextMessage(text, { run_immediately: true, audio_response: true });
    // A typed question has no STT transcript for the server to catch, so tell it
    // explicitly to log this as an audience question (spoken ones are detected
    // server-side). The presenter's answer is paired to it on the backend.
    if (client && connected) {
      client.sendMessage(createRtviClientMessage("qa-question", { text }));
    }
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
    sendText,
    notifyManualSlide,
    setPresentationFlow,
    get connected() {
      return connected;
    },
  };
}
