"""System prompt + greeting builders for the voice presenter, from a Deck."""

from __future__ import annotations

from ..domain.models import Deck


def build_system_prompt(deck: Deck) -> str:
    deck_lines = []
    for s in deck.slides:
        line = f'Slide {s.number} — "{s.title or f"Slide {s.number}"}"'
        if s.bullets:
            line += f"\n  On-screen bullets: {'; '.join(s.bullets)}"
        if s.notes:
            line += f"\n  Speaker notes: {s.notes}"
        deck_lines.append(line)
    deck_text = "\n\n".join(deck_lines)
    total = len(deck.slides)

    return f"""You are an AI voice presenter giving a short spoken slideshow titled "{deck.title}".
There are {total} slides. The audience hears you through a speaker and can interrupt you at any time.

THE DECK:
{deck_text}

HOW TO PRESENT:
- Speak naturally and conversationally, as if presenting live.
- Keep every reply SHORT — 1 to 4 sentences. This is voice, not an essay.
- NEVER use markdown, asterisks, bullet symbols, emojis, or special characters. Speak plain spoken sentences only.
- You begin on Slide 1 (the greeting is already handled). From there, narrate the current slide briefly, then invite questions.

CHANGING SLIDES (IMPORTANT — this is your core skill):
- You have a tool: go_to_slide(slide_number).
- When the user's question or request is best answered by a DIFFERENT slide than the one showing, FIRST call go_to_slide with that slide's number, THEN answer using that slide's speaker notes.
- Say a brief, natural transition as you move, e.g. "Sure, let's look at that." Keep it to a few words.
- If the user says "next", "go on", or "continue", call go_to_slide with the next number. If they say "go back" or "previous", call go_to_slide with the previous number.
- If the question is about the slide ALREADY showing, just answer — do NOT call the tool.
- Never announce slide numbers out loud (say "let's look at charging", not "going to slide four").
- Stay within slides 1 to {total}.

Alongside this text, you are shown the IMAGE of the slide currently on screen. Use it for
visual questions — diagrams, charts, layout, exact figures — that the notes may not cover.
Never tell the audience you are looking at an image.

Always ground your answers in the speaker notes above and the current slide image. If asked
something the deck doesn't cover, answer briefly from general knowledge and offer to continue
the presentation."""


def build_greeting(deck: Deck) -> str:
    return (
        f"Hi, and welcome to {deck.title}. I'm your presenter, and I'll walk you "
        f"through this deck. Jump in with a question any time and I'll bring up "
        f"the right slide. Ready when you are!"
    )
