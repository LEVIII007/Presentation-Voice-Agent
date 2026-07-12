"""Unit tests for the presentation state machine (app/voice/state.py).

These pin down the transitions behind the transcript bugs: stale linear
progress after navigation, restarted/dropped narration after barge-in, and
autopilot narration being mislogged as Q&A answers.
"""

from app.voice.state import PresentationState

FULL = (
    "With the geology in place, let's talk numbers. "
    "India ranks 24th in oil reserves. "
    "That shapes everything about our import strategy. "
    "Refining capacity, though, is world class."
)


def make(total: int = 10) -> PresentationState:
    ps = PresentationState(total)
    ps.autopilot_on = True
    return ps


# --- linear flow ---


def test_advance_walks_slides_in_order_and_claims_narration():
    ps = make(total=2)
    assert ps.advance() == (1, False)
    assert ps.narrating_slide == 1
    assert ps.advance() == (2, False)
    assert ps.advance() is None


def test_begin_closing_fires_once_and_disengages_autopilot():
    ps = make(total=1)
    ps.advance()
    assert ps.advance() is None
    assert ps.begin_closing() is True
    assert ps.autopilot_on is False
    assert ps.begin_closing() is False


# --- go_to_slide semantics ---


def test_qa_detour_does_not_move_linear_progress():
    ps = make()
    ps.advance()  # talk presented slide 1; next is 2
    ps.show_slide(7, navigation=False)  # question jumped ahead
    assert ps.current_slide == 7
    assert ps.advance() == (2, False)  # talk resumes where it left off


def test_qa_detour_sets_one_shot_return_marker():
    ps = make()
    ps.advance()
    ps.show_slide(7, navigation=False)
    assert ps.take_returning_from_qa_detour() is True
    assert ps.take_returning_from_qa_detour() is False


def test_navigation_reanchors_linear_progress():
    ps = make()
    ps.advance()  # next is 2
    ps.show_slide(5, navigation=True)  # "skip to slide 5"
    assert ps.advance() == (6, False)  # watchdog does NOT replay the old spot


def test_navigation_clears_pending_return_marker():
    ps = make()
    ps.advance()
    ps.show_slide(7, navigation=False)
    ps.show_slide(5, navigation=True)
    assert ps.take_returning_from_qa_detour() is False


def test_linear_return_to_a_jumped_slide_is_a_revisit():
    ps = make()
    ps.advance()  # slide 1
    ps.show_slide(2, navigation=False)  # Q&A showed slide 2 early
    assert ps.advance() == (2, True)


def test_manual_browser_flip_syncs_but_does_not_cover_or_advance():
    ps = make()
    ps.sync_manual_slide(4)
    assert ps.current_slide == 4
    assert 4 not in ps.covered
    assert ps.advance() == (1, False)
    ps.sync_manual_slide(99)  # clamped to deck bounds
    assert ps.current_slide == 10


# --- interruption tracking ---


def test_barge_in_mid_narration_parks_the_unheard_remainder():
    ps = make()
    ps.advance()  # narrating slide 1
    remainder = ps.user_speech_started(
        gen_text=FULL,
        spoken_text="With the geology in place, let's talk numbers. India ranks 24th in oil",
    )
    assert remainder is not None
    assert remainder.startswith("India ranks 24th in oil reserves.")
    assert ps.narrating_slide is None
    assert ps.take_interrupted() == (1, remainder)
    assert ps.narrating_slide == 1  # the finish cue is itself interruptible
    assert ps.take_interrupted() is None  # consumed


def test_fully_delivered_narration_is_not_a_false_interruption():
    ps = make()
    ps.advance()
    assert ps.user_speech_started(gen_text=FULL, spoken_text=FULL) is None
    assert ps.take_interrupted() is None
    # The narration claim is released, so interrupting the ANSWER that follows
    # never gets recorded as cut-off slide narration.
    assert ps.user_speech_started(gen_text="An answer.", spoken_text="") is None


def test_user_speech_without_narration_is_ignored():
    ps = make()
    assert ps.user_speech_started(gen_text=FULL, spoken_text="") is None


def test_navigation_drops_pending_interrupted_narration():
    ps = make()
    ps.advance()
    ps.user_speech_started(gen_text=FULL, spoken_text="")
    ps.show_slide(7, navigation=True)  # "skip to 7" — the audience moved on
    assert ps.take_interrupted() is None


# --- pause / resume replay ---


def test_capture_pause_buffers_from_the_interrupted_sentence():
    ps = make()
    text = ps.capture_pause(
        bot_speaking=True,
        full_text=FULL,
        spoken_text="With the geology in place, let's talk numbers. India ranks",
    )
    assert text.startswith("India ranks 24th in oil reserves.")
    assert ps.autopilot_on is False
    assert ps.take_resume_text() == text
    assert ps.take_resume_text() == ""  # consumed


def test_pause_while_quiet_buffers_nothing():
    ps = make()
    assert ps.capture_pause(bot_speaking=False, full_text=FULL, spoken_text="") == ""


# --- Q&A pairing ---


def test_qa_pair_records_only_after_fresh_bot_speech():
    ps = make()
    ps.qa_open("What about reserves?", slide=3)
    # An assistant turn re-emitted by a barge-in has no fresh speech start.
    assert ps.qa_record_answer_turn("old narration text", slide=3) is None
    ps.qa_on_bot_speech_start()
    snapshot = ps.qa_record_answer_turn("They are ranked 24th.", slide=3)
    assert snapshot == {
        "question": "What about reserves?",
        "answer": "They are ranked 24th.",
        "ask_slide": 3,
        "answer_slide": 3,
        "history": [],
    }
    pending = ps.qa_take_ready()
    assert pending == snapshot
    assert ps.qa_take_ready() is None  # consumed


def test_qa_merges_fragmented_user_question_before_answer_starts():
    ps = make()
    ps.qa_open("Can you directly tell me", slide=8, history=[{"role": "assistant", "content": "Generic AI is broad and blind.", "slide": 8}])
    ps.qa_open("how is it better than generic AI", slide=8)
    ps.qa_open("or what Mindtickle used before?", slide=8)
    ps.qa_on_bot_speech_start()
    ps.qa_record_answer_turn("Mineo uses real account context and conversation signals.", slide=8)
    pending = ps.qa_take_ready()
    assert pending is not None
    assert pending["question"] == (
        "Can you directly tell me how is it better than generic AI "
        "or what Mindtickle used before?"
    )
    assert pending["history"] == [
        {"role": "assistant", "content": "Generic AI is broad and blind.", "slide": 8}
    ]


def test_qa_keeps_collecting_answer_turns_until_flushed():
    ps = make()
    ps.qa_open("How much time will it save?", slide=5)
    ps.qa_on_bot_speech_start()
    ps.qa_record_answer_turn("From hours of manual prep down to minutes to deploy.", slide=9)
    ps.qa_record_answer_turn("It replaces 13 to 23 hours across research and testing.", slide=9)
    pending = ps.qa_take_ready()
    assert pending is not None
    assert pending["answer"] == (
        "From hours of manual prep down to minutes to deploy. "
        "It replaces 13 to 23 hours across research and testing."
    )
    assert pending["answer_slide"] == 9


def test_stage_cue_drops_pending_question():
    ps = make()
    ps.qa_open("What about reserves?", slide=3)
    ps.qa_reset()
    ps.qa_on_bot_speech_start()
    assert ps.qa_record_answer_turn("narration", slide=3) is None


def test_blank_question_does_not_arm_qa():
    ps = make()
    ps.qa_open("   ", slide=1)
    ps.qa_on_bot_speech_start()
    assert ps.qa_record_answer_turn("answer", slide=1) is None
