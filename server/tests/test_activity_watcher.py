"""Regression test for the autopilot stall after a barge-in storm.

A barge-in cancels the current turn and drains queued frames, so the
LLMFullResponseEndFrame that normally clears `llm_in_flight` can be dropped in
flight. Before the fix, that left the flag stuck True and `busy()` wedged the
autopilot watchdog permanently — the presentation froze until the user spoke
(observed live: session aa9a1b0ab851). The fix clears the turn-liveness flags
off the InterruptionFrame the interruption path guarantees reaches the tap.
"""

import asyncio

from pipecat.frames.frames import (
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from app.voice.session import _ActivityWatcher, _PresenterState


def _drive(state: _PresenterState, frames: list) -> None:
    """Run the frames through a real _ActivityWatcher in a minimal pipeline."""
    asyncio.run(run_test(_ActivityWatcher(state), frames_to_send=frames))


def test_barge_in_clears_llm_in_flight_when_end_frame_is_dropped():
    # LLM response started, then (seconds later, hence the SleepFrame so the
    # start frame is fully processed first) the user barged in — but the
    # matching LLMFullResponseEndFrame never arrives (dropped by the interruption).
    state = _PresenterState()
    _drive(state, [LLMFullResponseStartFrame(), SleepFrame(sleep=0.2), InterruptionFrame()])
    assert state.llm_in_flight is False  # was stuck True before the fix -> busy() wedged


def test_interruption_clears_all_turn_liveness_flags():
    # bot_speaking (BotStoppedSpeaking) and fn_pending (FunctionCallResult) share
    # the same drop-on-interruption risk, so the reset covers all three.
    state = _PresenterState()
    state.bot_speaking = True
    state.llm_in_flight = True
    state.fn_pending = 2
    _drive(state, [InterruptionFrame()])
    assert state.bot_speaking is False
    assert state.llm_in_flight is False
    assert state.fn_pending == 0


def test_normal_completion_still_clears_llm_in_flight():
    # Happy path unchanged: a response that ends cleanly clears the flag.
    state = _PresenterState()
    _drive(state, [LLMFullResponseStartFrame(), LLMFullResponseEndFrame()])
    assert state.llm_in_flight is False
