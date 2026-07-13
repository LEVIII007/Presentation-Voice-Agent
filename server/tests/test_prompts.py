"""Checks the live presenter prompt and kickoff cues."""

from app.domain.models import Deck, Slide
from app.services.prompts import (
    build_advance_cue,
    build_finish_cue,
    build_kickoff_cue,
    build_system_prompt,
)


def _deck() -> Deck:
    return Deck(
        id="deck1",
        title="Demo deck",
        source_filename="demo.pdf",
        intro="Welcome to the demo.",
        outro="Thanks for listening.",
        slides=[
            Slide(number=1, title="Overview", notes="We start with the main idea."),
            Slide(
                number=2,
                title="Details",
                transition="Now let's get concrete.",
                notes="The details live here.",
            ),
        ],
    )


def test_on_demand_prompt_mentions_look_at_slide():
    prompt = build_system_prompt(_deck())
    assert "look_at_slide" in prompt
    assert "Speaker notes are your PRIMARY source" in prompt
    assert "Alongside this text, you are shown the IMAGE of the slide currently on screen." not in prompt
    print("ok: on-demand prompt mentions look_at_slide")


def test_eager_prompt_preserves_current_slide_image_wording():
    prompt = build_system_prompt(_deck(), always_show_slide_image=True)
    assert "Alongside this text, you are shown the IMAGE of the slide currently on screen." in prompt
    assert "Ground every answer in the deck above and the current slide image" in prompt
    assert "look_at_slide" not in prompt
    print("ok: eager prompt preserves current-slide image wording")


def test_kickoff_cue_lets_model_decide_on_slide_1():
    cue = build_kickoff_cue()
    assert "title, agenda, or cover slide" in cue
    assert "go_to_slide with slide_number 1 and reason 'navigation'" in cue
    assert "opening only" in cue  # still the fallback when slide 1 is real content
    print("ok: kickoff cue lets the model merge or defer slide 1 based on its content")


def test_prompt_uses_generated_persona():
    deck = _deck()
    deck.persona = "An experienced operator who explains tradeoffs clearly and stays grounded."
    prompt = build_system_prompt(deck)
    assert "<persona>\nAn experienced operator who explains tradeoffs clearly and stays grounded." in prompt
    print("ok: generated persona is used")


def test_prompt_wraps_major_sections_in_xml_tags():
    prompt = build_system_prompt(_deck())
    for tag in (
        "opening",
        "closing",
        "deck",
        "presentation_flow",
        "qa_handling",
        "interruption_handling",
        "voice_rules",
        "visual_grounding",
    ):
        assert f"<{tag}>" in prompt and f"</{tag}>" in prompt, f"missing <{tag}> block"
    print("ok: major sections are wrapped in XML tags")


def test_prompt_wraps_each_slide_in_a_tagged_block():
    prompt = build_system_prompt(_deck())
    assert '<slide number="1">' in prompt
    assert "<title>Overview</title>" in prompt
    assert "<notes>We start with the main idea.</notes>" in prompt
    assert '<slide number="2">' in prompt
    assert "<transition>Now let's get concrete.</transition>" in prompt
    print("ok: each slide is its own tagged block within <deck>")


def test_prompt_mentions_multi_slide_answers_and_handbacks():
    prompt = build_system_prompt(_deck())
    assert "short 2-3 slide sequence" in prompt
    assert "go_to_slide for each needed slide FIRST" in prompt
    assert "returning to the main thread" in prompt
    print("ok: prompt allows multi-slide answers and requires hand-backs")


def test_prompt_requires_direct_conversational_answers():
    prompt = build_system_prompt(_deck())
    assert "Answer the PERSON, not just the content." in prompt
    assert 'Use "you" and "your" naturally' in prompt
    assert "talking with them in real time" in prompt
    print("ok: prompt requires direct conversational answers")


def test_prompt_without_pause_tag_has_no_pause_instruction():
    # No TTS backend capability given -> no pause-tag guidance, no exception
    # carved out of the "no special characters" voice rule, and no dangling
    # markup lingers, just the plain idea-group pacing guidance.
    prompt = build_system_prompt(_deck())
    assert "few short idea-groups" in prompt
    assert "natural stopping point on a completed thought" in prompt
    assert "<break" not in prompt
    assert "The one exception is the pause tag" not in prompt
    print("ok: prompt omits pause-tag guidance when the backend has no such capability")


def test_prompt_with_pause_tag_teaches_the_exact_markup():
    prompt = build_system_prompt(_deck(), pause_tag_example='<break time="0.6s" />')
    # The model is taught to emit the backend's own exact tag syntax.
    assert '<break time="0.6s" />' in prompt
    assert "your voice engine renders it as a real spoken pause" in prompt
    assert "Omit it and there is no pause" in prompt
    # The tag is the sanctioned exception to the "no special characters" rule.
    assert "The one exception is the pause tag described above" in prompt
    print("ok: prompt teaches the model the exact native pause tag when available")


def test_prompt_has_eye_guidance_and_acknowledgment_rules():
    prompt = build_system_prompt(_deck())
    # Guide their eyes, grounded only in what the slide actually shows.
    assert "Guide the audience's eyes" in prompt
    assert "the notes or the slide actually contain" in prompt
    # A bare acknowledgment is not a question.
    assert "Not every interjection is a question" in prompt
    print("ok: prompt has eye-guidance and acknowledgment rules")


def test_advance_cue_can_mark_return_from_detour():
    cue = build_advance_cue(3, returning_from_detour=True)
    assert "returning to the main flow" in cue
    assert "Coming back to where we left off" in cue
    print("ok: advance cue can request a return-from-detour hand-back")


def test_finish_cue_can_mark_return_from_detour():
    cue = build_finish_cue(3, "The rest goes here.", returning_from_detour=True)
    assert "returning to where you left off on this point" in cue
    print("ok: finish cue can request a return-from-detour hand-back")


if __name__ == "__main__":
    test_on_demand_prompt_mentions_look_at_slide()
    test_eager_prompt_preserves_current_slide_image_wording()
    test_kickoff_cue_lets_model_decide_on_slide_1()
    test_prompt_uses_generated_persona()
    test_prompt_wraps_major_sections_in_xml_tags()
    test_prompt_wraps_each_slide_in_a_tagged_block()
    test_prompt_mentions_multi_slide_answers_and_handbacks()
    test_prompt_requires_direct_conversational_answers()
    test_prompt_without_pause_tag_has_no_pause_instruction()
    test_prompt_with_pause_tag_teaches_the_exact_markup()
    test_prompt_has_eye_guidance_and_acknowledgment_rules()
    test_advance_cue_can_mark_return_from_detour()
    test_finish_cue_can_mark_return_from_detour()
    print("\nAll prompt tests passed.")
