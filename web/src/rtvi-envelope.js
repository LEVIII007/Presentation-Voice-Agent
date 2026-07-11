export function createRtviClientEnvelope(type, data) {
  return {
    type: "client-message",
    data: { t: type, d: data },
  };
}

export function createPresentationFlowEnvelope(action) {
  return createRtviClientEnvelope("presentation-flow", { action });
}
