"""Helpers for injecting slide images into the live LLM context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SlideImageContextState:
    """Tracks whether an in-context slide image should persist across turns."""

    slide_number: int | None = None
    temporary: bool = False


def is_slide_image_message(msg: Any) -> bool:
    """Identify the injected slide-image context message in the LLM history."""

    if not isinstance(msg, dict) or msg.get("role") != "user":
        return False
    content = msg.get("content")
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )


def build_slide_image_message(slide_number: int, image_b64: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    f"(System context, not spoken by the audience: this is the image of "
                    f"slide {slide_number}. Use it only when you need visual details — "
                    f"charts, diagrams, layout, or exact on-screen values — that the "
                    f"speaker notes do not cover. Never mention that you are shown "
                    f"images or that you used a tool.)"
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
            },
        ],
    }


def clear_slide_image(messages: list[dict[str, Any]], state: SlideImageContextState) -> None:
    messages[:] = [m for m in messages if not is_slide_image_message(m)]
    state.slide_number = None
    state.temporary = False


def set_slide_image(
    messages: list[dict[str, Any]],
    state: SlideImageContextState,
    *,
    slide_number: int,
    image_b64: str,
    temporary: bool,
) -> None:
    """Keep exactly one slide image in the context, inserted after the system prompt."""

    clear_slide_image(messages, state)
    messages.insert(1, build_slide_image_message(slide_number, image_b64))
    state.slide_number = slide_number
    state.temporary = temporary


def clear_temporary_slide_image(
    messages: list[dict[str, Any]], state: SlideImageContextState
) -> None:
    if state.temporary:
        clear_slide_image(messages, state)
