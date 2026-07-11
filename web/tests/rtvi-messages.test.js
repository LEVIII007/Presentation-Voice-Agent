import assert from "node:assert/strict";
import test from "node:test";

import {
  createPresentationFlowEnvelope,
  createRtviClientEnvelope,
} from "../src/rtvi-envelope.js";

test("presentation flow messages use the client-message envelope", () => {
  const message = createPresentationFlowEnvelope("pause");

  assert.equal(message.type, "client-message");
  assert.deepEqual(message.data, {
    t: "presentation-flow",
    d: { action: "pause" },
  });
});

test("custom browser messages preserve their inner type and payload", () => {
  const message = createRtviClientEnvelope("qa-question", { text: "What changed?" });

  assert.equal(message.type, "client-message");
  assert.deepEqual(message.data, {
    t: "qa-question",
    d: { text: "What changed?" },
  });
});
