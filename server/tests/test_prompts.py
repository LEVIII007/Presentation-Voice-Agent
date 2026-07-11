"""Checks the live presenter prompt and kickoff cues."""

from app.domain.models import Deck, Slide
from app.services.prompts import build_kickoff_cue, build_system_prompt


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


def test_kickoff_cue_only_delivers_opening():
    cue = build_kickoff_cue()
    assert "opening only" in cue
    assert "Do not present slide 1 yet" in cue
    print("ok: kickoff cue keeps slide 1 for the interruptible flow")


def test_prompt_uses_generated_persona_when_no_override():
    deck = _deck()
    deck.persona = "An experienced operator who explains tradeoffs clearly and stays grounded."
    prompt = build_system_prompt(deck)
    assert "WHO YOU ARE: An experienced operator who explains tradeoffs clearly and stays grounded." in prompt
    print("ok: generated persona is used by default")


def test_prompt_prefers_user_persona_override():
    deck = _deck()
    deck.persona = "A polished consultant with boardroom language."
    deck.persona_override = "A friendly founder with high energy, simple language, and a conversational pace."
    prompt = build_system_prompt(deck)
    assert "WHO YOU ARE: A friendly founder with high energy, simple language, and a conversational pace." in prompt
    assert "WHO YOU ARE: A polished consultant with boardroom language." not in prompt
    print("ok: user persona override wins")


if __name__ == "__main__":
    test_on_demand_prompt_mentions_look_at_slide()
    test_eager_prompt_preserves_current_slide_image_wording()
    test_kickoff_cue_only_delivers_opening()
    test_prompt_uses_generated_persona_when_no_override()
    test_prompt_prefers_user_persona_override()
    print("\nAll prompt tests passed.")
