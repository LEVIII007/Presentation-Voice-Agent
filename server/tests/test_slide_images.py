"""Checks slide-image context helpers used by the live voice session."""

import asyncio

from pipecat.frames.frames import LLMFullResponseStartFrame
from pipecat.processors.frame_processor import FrameDirection

from app.voice.session import _ActivityWatcher, _PresenterState
from app.voice.slide_images import (
    SlideImageContextState,
    clear_temporary_slide_image,
    is_slide_image_message,
    set_slide_image,
)


def _count_image_messages(messages) -> int:
    return sum(1 for msg in messages if is_slide_image_message(msg))


def test_set_slide_image_keeps_exactly_one_message_after_system():
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello"},
    ]
    state = SlideImageContextState()

    set_slide_image(messages, state, slide_number=2, image_b64="abc", temporary=True)
    assert _count_image_messages(messages) == 1
    assert is_slide_image_message(messages[1])
    assert "slide 2" in messages[1]["content"][0]["text"]
    assert messages[1]["content"][1]["image_url"]["url"].endswith("abc")

    set_slide_image(messages, state, slide_number=4, image_b64="def", temporary=True)
    assert _count_image_messages(messages) == 1
    assert is_slide_image_message(messages[1])
    assert "slide 4" in messages[1]["content"][0]["text"]
    assert messages[1]["content"][1]["image_url"]["url"].endswith("def")
    assert state.slide_number == 4
    assert state.temporary is True
    print("ok: set_slide_image keeps one image message")


def test_clear_temporary_slide_image_leaves_persistent_image_alone():
    messages = [{"role": "system", "content": "system prompt"}]
    state = SlideImageContextState()

    set_slide_image(messages, state, slide_number=1, image_b64="persist", temporary=False)
    clear_temporary_slide_image(messages, state)

    assert _count_image_messages(messages) == 1
    assert state.slide_number == 1
    assert state.temporary is False
    print("ok: persistent image survives temporary cleanup")


def test_temporary_image_is_cleared_on_next_llm_response_start():
    messages = [{"role": "system", "content": "system prompt"}]
    image_state = SlideImageContextState()
    presenter_state = _PresenterState()
    set_slide_image(messages, image_state, slide_number=3, image_b64="temp", temporary=True)

    async def cleanup():
        clear_temporary_slide_image(messages, image_state)

    async def noop_push(_frame, _direction):
        return None

    watcher = _ActivityWatcher(presenter_state, on_llm_response_start=cleanup)
    watcher.push_frame = noop_push

    asyncio.run(watcher.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM))

    assert _count_image_messages(messages) == 0
    assert image_state.slide_number is None
    assert image_state.temporary is False
    assert presenter_state.llm_in_flight is True
    print("ok: temporary image cleared on llm response start")


if __name__ == "__main__":
    test_set_slide_image_keeps_exactly_one_message_after_system()
    test_clear_temporary_slide_image_leaves_persistent_image_alone()
    test_temporary_image_is_cleared_on_next_llm_response_start()
    print("\nAll slide image tests passed.")
