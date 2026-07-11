import { RTVIMessage } from "@pipecat-ai/client-js";

import {
  createPresentationFlowEnvelope,
  createRtviClientEnvelope,
} from "./rtvi-envelope.js";

// Pipecat only dispatches custom browser payloads to on_client_message when
// they arrive inside the RTVI "client-message" envelope.
export function createRtviClientMessage(type, data) {
  const message = createRtviClientEnvelope(type, data);
  return new RTVIMessage(message.type, message.data);
}

export function createPresentationFlowMessage(action) {
  const message = createPresentationFlowEnvelope(action);
  return new RTVIMessage(message.type, message.data);
}
