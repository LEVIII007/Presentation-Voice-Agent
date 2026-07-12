"""Single authoritative store for where the presentation IS.

Everything mutable about the presentation's position and flow lives here:
current slide, linear progress, coverage, autopilot, narration/interruption
tracking, the button-pause replay buffer, and Q&A pairing. The voice session
owns exactly one instance. The two other places that "know" the state are
projections of this object, never peers:

- the LLM's context sees it only through stage-direction cues and tool
  results the session derives from here;
- the browser mirrors it through RTVI pushes (go_to_slide frames, flow acks)
  and reports manual slide flips back via sync_manual_slide().

Pipeline *activity* (is the bot speaking, is a completion in flight) is
deliberately NOT here — that is a liveness measurement owned by the frame
processors in session.py (_PresenterState), not presentation position.

Multi-field updates go through the transition methods below so callers cannot
half-update related fields — the class of bug where go_to_slide moved the
current slide but left the linear progress pointer stale, so the watchdog
replayed slides the audience had already seen.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from pipecat.utils.string import match_endofsentence
except ImportError:  # pragma: no cover - lightweight test environments
    _EOS_RE = re.compile(r".*?[.!?](?:['\")\]]+)?(?:\s+|$)", re.S)

    def match_endofsentence(text: str) -> int:
        match = _EOS_RE.match(text or "")
        return match.end() if match else -1


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using the same detector the TTS path uses, so a
    'sentence' here is exactly what the presenter spoke as one unit."""
    sentences: list[str] = []
    rest = text.strip()
    while rest:
        idx = match_endofsentence(rest)
        if idx <= 0:  # no complete sentence left; keep the remainder as one
            sentences.append(rest)
            break
        sentences.append(rest[:idx].strip())
        rest = rest[idx:].strip()
    return [s for s in sentences if s]


def _completed_sentence_count(text: str) -> int:
    """How many *finished* sentences the text contains — a trailing fragment with
    no sentence-ending punctuation does not count. This is the index of the
    interrupted sentence within the full narration when `text` is what was spoken."""
    count = 0
    rest = text.strip()
    while rest:
        idx = match_endofsentence(rest)
        if idx <= 0:  # trailing fragment, still mid-sentence
            break
        count += 1
        rest = rest[idx:].strip()
    return count


def _unheard_remainder(full_text: str, spoken_text: str) -> str:
    """The sentences of full_text the audience never heard, given the
    playback-aligned spoken_text at the moment of the cut. Sentences finished
    before the cut were heard; everything after — including the sentence that
    got chopped mid-way — was not."""
    full = full_text.strip()
    if not full:
        return ""
    sentences = _split_sentences(full)
    spoken = spoken_text.strip()
    done = _completed_sentence_count(spoken) if spoken else 0
    done = max(0, min(done, len(sentences)))
    return " ".join(sentences[done:]).strip()


class PresentationState:
    """The presentation's position and flow. See module docstring for ownership."""

    def __init__(self, slide_total: int) -> None:
        self.slide_total = slide_total
        # What the audience sees on screen right now.
        self.current_slide = 1
        # Next slide in the LINEAR talk flow. Q&A detours move current_slide
        # but not this, so after a digression the talk resumes where it was;
        # explicit navigation re-anchors it (see show_slide).
        self.next_slide = 1
        self.outro_done = False
        # Engaged on connect, disengaged by the pause tool or after the closing.
        self.autopilot_on = False
        # Slides substantively presented or discussed so far — the linear walk
        # and Q&A jumps both count. When the linear flow later reaches one of
        # these, the presenter acknowledges it instead of repeating it verbatim.
        self.covered: set[int] = set()
        # A Q&A detour to another slide should be followed by a short "back to
        # where we were" hand-back when the linear talk resumes. The next
        # autopilot cue consumes this flag.
        self.returning_from_qa_detour = False
        # The slide the current (or most recent) completion is presenting — set
        # when an advance/finish cue goes out, released by the first user
        # speech after it. If that speech cut the narration off mid-delivery,
        # the unheard remainder is parked below; the watchdog then cues the
        # presenter to FINISH that slide before advancing.
        self.narrating_slide: int | None = None
        self.interrupted_slide: int | None = None
        self.interrupted_remainder = ""
        # Button-pause replay buffer: the narration from the interrupted
        # sentence onward, re-spoken verbatim on resume (no LLM round-trip).
        self.resume_text = ""
        # Q&A pairing: a question is "pending" from when it's asked until the
        # presenter finishes answering it. Consecutive user transcript turns
        # before the answer starts are merged into one question so STT-split
        # asks like "how does it" + "do that" are logged as one exchange, not
        # as the last fragment only. Autopilot narration must never be logged
        # as an answer: `answering` is armed only by a real question, and
        # recording answer text also requires `answer_started` — a FRESH
        # bot-speech-start AFTER the question. Every stage cue resets this,
        # dropping any pending pair.
        self.qa_pending: dict[str, Any] | None = None
        self.qa_answering = False
        self.qa_answer_started = False

    # --- slide position ---

    def show_slide(self, n: int, *, navigation: bool) -> None:
        """The presenter put slide n on screen (go_to_slide tool). An explicit
        "next / back / skip to" (navigation=True) re-anchors the linear talk:
        the watchdog continues from after this slide instead of replaying
        wherever the flow stood before the jump, and any pending interrupted
        narration is dropped — the audience moved on. A Q&A detour
        (navigation=False) leaves both alone so the talk resumes where it was."""
        old_current = self.current_slide
        self.current_slide = n
        self.covered.add(n)
        if navigation:
            self.next_slide = n + 1
            self.clear_interrupted()
            self.returning_from_qa_detour = False
        elif n != old_current:
            self.returning_from_qa_detour = True

    def sync_manual_slide(self, n: int) -> None:
        """The audience flipped the slide in the browser. Mirror it so slide
        tags and image lookups stay truthful — but a mere look neither counts
        as covered nor moves the linear flow."""
        self.current_slide = max(1, min(self.slide_total, n))

    # --- linear flow (consumed by the autopilot watchdog) ---

    def advance(self) -> tuple[int, bool] | None:
        """Consume the next linear slide and claim it as the narration turn.
        Returns (slide_number, is_revisit), or None when past the last slide."""
        n = self.next_slide
        if n > self.slide_total:
            return None
        self.next_slide = n + 1
        revisit = n in self.covered  # a Q&A jump already showed this slide
        self.covered.add(n)
        self.narrating_slide = n
        return n, revisit

    def take_returning_from_qa_detour(self) -> bool:
        """True once after a Q&A slide detour, then reset."""
        returning = self.returning_from_qa_detour
        self.returning_from_qa_detour = False
        return returning

    def begin_closing(self) -> bool:
        """True exactly once, when the talk has run out of slides. Autopilot
        disengages; Q&A from here on, the user drives."""
        if self.outro_done:
            return False
        self.outro_done = True
        self.autopilot_on = False
        return True

    # --- narration interruption (voice barge-in) ---

    def user_speech_started(self, *, gen_text: str, spoken_text: str) -> str | None:
        """User speech releases any narration claim. If it cut the narration
        off mid-delivery, park the unheard remainder for the watchdog and
        return it; returns None when nothing was missed (or nothing was
        being narrated)."""
        slide_n = self.narrating_slide
        if slide_n is None:
            return None
        self.narrating_slide = None
        remainder = _unheard_remainder(gen_text, spoken_text)
        if not remainder:
            return None
        self.interrupted_slide = slide_n
        self.interrupted_remainder = remainder
        return remainder

    def take_interrupted(self) -> tuple[int, str] | None:
        """Pop the pending interrupted narration, claiming it as the new
        narration turn (the finish cue is itself interruptible)."""
        if self.interrupted_slide is None or not self.interrupted_remainder:
            return None
        n, remainder = self.interrupted_slide, self.interrupted_remainder
        self.clear_interrupted()
        self.narrating_slide = n
        return n, remainder

    def clear_interrupted(self) -> None:
        self.interrupted_slide = None
        self.interrupted_remainder = ""

    # --- button pause/resume replay ---

    def capture_pause(self, *, bot_speaking: bool, full_text: str, spoken_text: str) -> str:
        """Button pause: buffer the narration from the interrupted sentence
        onward so resume can replay it verbatim. Returns what was buffered
        ("" when the bot was not mid-speech). Also disengages autopilot."""
        resume_text = ""
        if bot_speaking and full_text:
            resume_text = _unheard_remainder(full_text, spoken_text)
        self.resume_text = resume_text
        self.autopilot_on = False
        return resume_text

    def take_resume_text(self) -> str:
        text = self.resume_text
        self.resume_text = ""
        return text

    # --- Q&A pairing ---

    def qa_open(self, question: str, slide: int, history: list[dict] | None = None) -> None:
        q = (question or "").strip()
        if not q:
            return
        # Merge consecutive user transcript turns into one pending question
        # until the presenter actually starts answering.
        if self.qa_pending is not None and not self.qa_answer_started:
            parts = self.qa_pending.setdefault("question_parts", [])
            if not parts or parts[-1] != q:
                parts.append(q)
            self.qa_pending["question"] = " ".join(
                part for part in parts if str(part or "").strip()
            ).strip()
            return
        # `history` is a snapshot of the recent conversation before the FIRST
        # fragment of this ask, carried through so the extractor can judge
        # intent in context and resolve short follow-ups if they are actually
        # clear from what just happened.
        self.qa_pending = {
            "question_parts": [q],
            "question": q,
            "ask_slide": slide,
            "history": history or [],
            "answer_parts": [],
            "answer": "",
            "answer_slide": None,
        }
        self.qa_answering = True
        self.qa_answer_started = False

    def qa_reset(self) -> None:
        self.qa_pending = None
        self.qa_answering = False
        self.qa_answer_started = False

    def qa_on_bot_speech_start(self) -> None:
        if self.qa_answering:
            self.qa_answer_started = True

    def qa_record_answer_turn(self, answer: str, slide: int) -> dict[str, Any] | None:
        """Append one assistant transcript turn to the pending answer, if this
        really is the fresh answer to a real audience question. A barge-in that
        cut narration re-emits an older turn with no fresh bot-speech-start, so
        it never records answer text."""
        if not (self.qa_answering and self.qa_answer_started and self.qa_pending):
            return None
        a = (answer or "").strip()
        if not a:
            return None
        parts = self.qa_pending.setdefault("answer_parts", [])
        if not parts or parts[-1] != a:
            parts.append(a)
        self.qa_pending["answer"] = " ".join(
            part for part in parts if str(part or "").strip()
        ).strip()
        self.qa_pending["answer_slide"] = slide
        return {
            "question": self.qa_pending["question"],
            "answer": self.qa_pending["answer"],
            "ask_slide": self.qa_pending["ask_slide"],
            "answer_slide": self.qa_pending["answer_slide"],
            "history": list(self.qa_pending.get("history", [])),
        }

    def qa_take_ready(self) -> dict[str, Any] | None:
        """Pop the pending Q&A exchange once the answer has settled."""
        if self.qa_pending is None:
            return None
        pending = self.qa_pending
        self.qa_reset()
        question = str(pending.get("question", "") or "").strip()
        answer = str(pending.get("answer", "") or "").strip()
        if not question or not answer:
            return None
        return {
            "question": question,
            "answer": answer,
            "ask_slide": pending.get("ask_slide"),
            "answer_slide": pending.get("answer_slide"),
            "history": list(pending.get("history", [])),
        }
