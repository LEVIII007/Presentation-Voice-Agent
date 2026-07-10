// RTVI voice session wrapper — connects to the backend's per-deck pipeline.
// Adapted from the original single-page demo; now parameterized by deck.

import { RTVIClient, RTVIEvent, RTVIMessage } from "@pipecat-ai/client-js";
import { WebSocketTransport } from "@pipecat-ai/websocket-transport";

import { BACKEND_URL } from "./api.js";

function parseServerMessage(message) {
  if (message && message.data && message.data.message_type) return message.data;
  if (message && message.message_type) return message;
  if (message && message.type === "server-message" && message.data) return message.data;
  return null;
}

// States reported via onStatus: connecting | connected | speaking | listening | thinking | offline | error
export function createVoiceSession({ deckId, enableMic = true, onStatus, onCaption, onSlide, onDisconnected }) {
  let client = null;
  let connected = false;

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
          connected = true;
          onStatus("connected", "Listening");
          onCaption("Listening — ask a question any time, or just start talking to interrupt.");
        },
        onDisconnected: () => {
          connected = false;
          onStatus("offline", "Session ended");
          onDisconnected && onDisconnected();
        },
        onServerMessage: (message) => {
          const data = parseServerMessage(message);
          if (!data) return;
          if (data.message_type === "go_to_slide" && typeof data.slide === "number") {
            onSlide(data.slide);
          }
        },
        onError: (err) => {
          const msg = typeof err === "object" ? JSON.stringify(err) : String(err);
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
    client.on(RTVIEvent.BotTranscript, (data) => {
      if (data && data.text) onCaption(`<b>Presenter:</b> ${data.text}`);
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
    if (!client || !connected) return;
    client.sendMessage(
      new RTVIMessage("send-text", {
        content: text,
        options: { run_immediately: true, audio_response: true },
      }),
    );
  }

  // Manual slide jump -> silently tell the agent so its mental model of "the
  // current slide" stays in sync, without making it speak.
  function notifyManualSlide(n, title) {
    if (!client || !connected) return;
    client.sendMessage(
      new RTVIMessage("send-text", {
        content: `(The audience manually moved to slide ${n}${title ? `, titled "${title}"` : ""}. Continue from here when relevant; do not announce this move.)`,
        options: { run_immediately: false, audio_response: false },
      }),
    );
  }

  return {
    connect,
    disconnect,
    sendText,
    notifyManualSlide,
    get connected() {
      return connected;
    },
  };
}
