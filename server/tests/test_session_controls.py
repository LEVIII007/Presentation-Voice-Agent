"""Regression tests for browser presentation-flow control messages."""

from types import SimpleNamespace

from app.voice.session import _parse_client_message


def test_parse_presentation_flow_message_accepts_direct_shape():
    message = SimpleNamespace(type="presentation-flow", data={"action": "pause"})

    message_type, data = _parse_client_message(message, "presentation-flow")

    assert message_type == "presentation-flow"
    assert data == {"action": "pause"}


def test_parse_presentation_flow_message_accepts_wrapped_client_message():
    message = SimpleNamespace(
        type="client-message",
        data={"t": "presentation-flow", "d": {"action": "resume"}},
    )

    message_type, data = _parse_client_message(message, "presentation-flow")

    assert message_type == "presentation-flow"
    assert data == {"action": "resume"}


def test_parse_presentation_flow_message_ignores_unrelated_messages():
    message = {"type": "client-message", "data": {"t": "something-else", "d": {"action": "pause"}}}

    message_type, data = _parse_client_message(message, "presentation-flow")

    assert message_type is None
    assert data == {}
