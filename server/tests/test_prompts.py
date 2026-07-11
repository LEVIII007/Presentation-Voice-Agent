"""Checks the live presenter prompt in eager vs on-demand vision modes."""

from app.domain.models import Deck, Slide
from app.services.prompts import build_system_prompt


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


if __name__ == "__main__":
    test_on_demand_prompt_mentions_look_at_slide()
    test_eager_prompt_preserves_current_slide_image_wording()
    print("\nAll prompt tests passed.")
