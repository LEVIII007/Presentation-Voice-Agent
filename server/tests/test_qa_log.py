"""Checks Q&A log prompt/context assembly and extractor plumbing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.adapters.qa_log import _SYSTEM, _build_user_content, AzureQAExtractor
from app.voice.history import recent_turns


class _FakeCompletions:
    def __init__(self, response_content: str):
        self._response_content = response_content
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._response_content))]
        )


class _FakeClient:
    def __init__(self, response_content: str):
        self.completions = _FakeCompletions(response_content)
        self.chat = SimpleNamespace(completions=self.completions)


def test_recent_turns_keeps_last_8_with_slide_metadata():
    turns = [
        {"role": "assistant" if i % 2 == 0 else "user", "content": f"turn-{i}", "slide": i}
        for i in range(10)
    ]

    snapshot = recent_turns(turns)

    assert len(snapshot) == 8
    assert snapshot[0] == {"role": "assistant", "content": "turn-2", "slide": 2}
    assert snapshot[-1] == {"role": "user", "content": "turn-9", "slide": 9}
    print("ok: recent_turns keeps the last 8 turns with slide metadata")


def test_build_user_content_includes_slide_context_and_history():
    content = _build_user_content(
        utterance="Can you explain this in more detail?",
        reply="It records Q's local state right away, then keeps logging messages still in transit.",
        deck_title="Chandy-Lamport Snapshot",
        ask_slide_number=16,
        ask_slide_title="Recording In-Transit Messages",
        answer_slide_number=16,
        answer_slide_title="Recording In-Transit Messages",
        history=[
            {
                "role": "assistant",
                "content": "Next, we can trace exactly how incoming messages are recorded.",
                "slide": 16,
            },
            {
                "role": "user",
                "content": "Can you explain this in more detail?",
                "slide": 16,
            },
        ],
    )

    assert 'PRESENTATION: "Chandy-Lamport Snapshot"' in content
    assert 'ASKED ON: Slide 16 — "Recording In-Transit Messages"' in content
    assert 'ANSWERED ON: Slide 16 — "Recording In-Transit Messages"' in content
    assert "- Presenter (slide 16): Next, we can trace exactly how incoming messages are recorded." in content
    assert '- Audience (slide 16): Can you explain this in more detail?' in content
    assert 'AUDIENCE SAID:\n"Can you explain this in more detail?"' in content
    assert 'PRESENTER REPLIED:\n"It records Q\'s local state right away, then keeps logging messages still in transit."' in content
    print("ok: user content includes ask/answer slide context and slide-tagged history")


def test_system_prompt_requires_self_contained_fail_closed_questions():
    assert "MUST stand on its own a week later" in _SYSTEM
    assert 'Resolve vague references like "this", "that", "it"' in _SYSTEM
    assert "asked-from slide topic" in _SYSTEM
    assert "presentation title" in _SYSTEM
    assert "If that referent is STILL ambiguous after using the context, set is_question=false." in _SYSTEM
    assert "do not" in _SYSTEM.lower() and "guess" in _SYSTEM.lower()
    print("ok: system prompt requires self-contained questions and fail-closed ambiguity handling")


def test_extract_uses_richer_context_and_parses_question():
    fake = _FakeClient(
        '{"is_question": true, "question": "What is the global state in this snapshot?", '
        '"answer": "It combines each process state with the current channel contents."}'
    )
    extractor = AzureQAExtractor(
        api_key="",
        endpoint="",
        api_version="",
        deployment="qa-demo",
        client=fake,
    )

    result = asyncio.run(
        extractor.extract(
            utterance="What is the global state?",
            reply="It combines each process state with the current channel contents.",
            deck_title="Chandy-Lamport Snapshot",
            ask_slide_number=20,
            ask_slide_title="Global State Snapshot",
            answer_slide_number=20,
            answer_slide_title="Global State Snapshot",
            history=[
                {
                    "role": "assistant",
                    "content": "In this state, p1 has local state under 900,0 and p2 has 50,2000.",
                    "slide": 20,
                }
            ],
        )
    )

    assert result is not None
    assert result.question == "What is the global state in this snapshot?"
    assert result.answer == "It combines each process state with the current channel contents."
    assert len(fake.completions.calls) == 1
    call = fake.completions.calls[0]
    assert call["model"] == "qa-demo"
    assert call["messages"][0]["content"] == _SYSTEM
    assert 'ASKED ON: Slide 20 — "Global State Snapshot"' in call["messages"][1]["content"]
    assert 'ANSWERED ON: Slide 20 — "Global State Snapshot"' in call["messages"][1]["content"]
    assert "- Presenter (slide 20): In this state, p1 has local state under 900,0 and p2 has 50,2000." in call["messages"][1]["content"]
    print("ok: extractor sends richer context and parses the model response")


def test_extract_returns_none_when_model_rejects_ambiguous_followup():
    fake = _FakeClient('{"is_question": false, "question": "", "answer": ""}')
    extractor = AzureQAExtractor(
        api_key="",
        endpoint="",
        api_version="",
        deployment="qa-demo",
        client=fake,
    )

    result = asyncio.run(
        extractor.extract(
            utterance="Can you give me a direct example?",
            reply="Sure.",
            deck_title="Chandy-Lamport Snapshot",
            ask_slide_number=2,
            ask_slide_title="Why Consistency Matters",
            answer_slide_number=5,
            answer_slide_title="Channel In Transit",
            history=[],
        )
    )

    assert result is None
    print("ok: extractor drops entries the model marks as too ambiguous to keep")


if __name__ == "__main__":
    test_recent_turns_keeps_last_8_with_slide_metadata()
    test_build_user_content_includes_slide_context_and_history()
    test_system_prompt_requires_self_contained_fail_closed_questions()
    test_extract_uses_richer_context_and_parses_question()
    test_extract_returns_none_when_model_rejects_ambiguous_followup()
    print("\nAll Q&A log tests passed.")
