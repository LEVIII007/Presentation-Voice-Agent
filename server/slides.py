"""Slide deck definition and prompt/display helpers.

Single source of truth for the deck. Each slide carries:
- `title` / `bullets`: what the browser renders.
- `notes`: speaker notes the LLM uses to narrate and answer questions.

`display_slides()` feeds the frontend (title + bullets only).
`build_system_prompt()` feeds the LLM (everything, numbered).
"""

from typing import Any, Dict, List

TOPIC = "Electric Vehicles 101"

SLIDES: List[Dict[str, Any]] = [
    {
        "title": "Electric Vehicles 101",
        "bullets": [
            "How EVs work, batteries, charging, cost, and the road ahead",
            "Ask a question any time — I'll jump to the right slide",
            "Six slides, roughly five minutes",
        ],
        "notes": (
            "This is the title / agenda slide. Welcome the audience to a short talk on "
            "electric vehicles. The talk covers: how an EV works, battery technology, "
            "charging, cost and savings, and future adoption. Encourage the listener to "
            "interrupt with questions at any time."
        ),
    },
    {
        "title": "How an EV Works",
        "bullets": [
            "Battery pack feeds an inverter, which drives an electric motor",
            "No engine, no gears, no exhaust — near-instant torque",
            "Regenerative braking recovers energy back into the battery",
        ],
        "notes": (
            "An EV stores energy in a battery pack. An inverter converts the battery's DC "
            "power into AC to drive one or more electric motors, which turn the wheels. "
            "There is no internal combustion engine, no multi-speed gearbox, and no tailpipe. "
            "Electric motors deliver near-instant torque, so acceleration feels immediate. "
            "Regenerative braking uses the motor as a generator when slowing down, feeding "
            "energy back into the battery and extending range."
        ),
    },
    {
        "title": "Battery Technology",
        "bullets": [
            "Lithium-ion packs, typically 40–100 kWh",
            "Range: roughly 250–500 km on a full charge",
            "Modern packs keep ~90% health after 8–10 years",
        ],
        "notes": (
            "Most EVs use lithium-ion battery packs, commonly between 40 and 100 kilowatt-hours. "
            "Battery capacity is the biggest driver of range: a typical EV goes about 250 to 500 "
            "kilometers on a full charge, depending on pack size, speed, and weather. "
            "Batteries degrade slowly — modern packs typically retain around 90 percent of their "
            "capacity after 8 to 10 years, and most come with an 8-year warranty. This is the slide "
            "to use for any question about range, how far an EV can go, battery size, or battery life."
        ),
    },
    {
        "title": "Charging",
        "bullets": [
            "Level 1 (home outlet): slow, overnight top-ups",
            "Level 2 (home/public): a full charge in 4–8 hours",
            "DC fast charging: 10–80% in about 20–40 minutes",
        ],
        "notes": (
            "There are three main ways to charge. Level 1 uses a standard household outlet and is "
            "slow — good for overnight top-ups only. Level 2 uses a 240-volt connection at home or "
            "in public and fully charges most EVs in 4 to 8 hours. DC fast charging is the fastest, "
            "taking a battery from 10 to 80 percent in roughly 20 to 40 minutes. Most owners charge "
            "at home overnight and use fast chargers on long trips. Use this slide for any question "
            "about charging, plugs, charging time, or charging at home versus in public."
        ),
    },
    {
        "title": "Cost & Savings",
        "bullets": [
            "Higher sticker price, lower running costs",
            "Fuel savings: electricity is far cheaper per km than petrol",
            "Fewer moving parts means lower maintenance; incentives can cut the price",
        ],
        "notes": (
            "EVs usually cost more up front than comparable petrol cars, but they are cheaper to run. "
            "Charging on electricity costs far less per kilometer than buying petrol or diesel. "
            "Maintenance is lower too — no oil changes, fewer moving parts, and less brake wear thanks "
            "to regenerative braking. Many regions offer purchase incentives, tax credits, or rebates "
            "that narrow the price gap. Over the life of the car, total cost of ownership is often "
            "lower than an equivalent gas car. Use this slide for any question about price, cost, how "
            "much an EV costs, savings, running costs, or incentives."
        ),
    },
    {
        "title": "The Road Ahead",
        "bullets": [
            "EV sales are growing fast worldwide",
            "Charging networks and battery tech keep improving",
            "Falling battery costs are pushing toward price parity with petrol cars",
        ],
        "notes": (
            "EV adoption is accelerating globally as prices fall and charging networks expand. "
            "Battery costs have dropped dramatically over the past decade, pushing EVs toward price "
            "parity with petrol cars. Charging is getting faster and more widely available, and range "
            "keeps improving. The direction of travel is clear: EVs are moving from niche to mainstream. "
            "This is the closing slide — wrap up and thank the audience. Use it for questions about the "
            "future, adoption trends, or what comes next."
        ),
    },
]

SLIDE_COUNT = len(SLIDES)


def display_slides() -> List[Dict[str, Any]]:
    """Return the display payload for the frontend (no speaker notes)."""
    return [
        {"number": i + 1, "title": s["title"], "bullets": s["bullets"]}
        for i, s in enumerate(SLIDES)
    ]


def build_system_prompt() -> str:
    """Build the LLM system prompt, embedding the full numbered deck."""
    deck_lines = []
    for i, s in enumerate(SLIDES):
        n = i + 1
        bullets = "; ".join(s["bullets"])
        deck_lines.append(
            f"Slide {n} — \"{s['title']}\"\n"
            f"  On-screen bullets: {bullets}\n"
            f"  Speaker notes: {s['notes']}"
        )
    deck = "\n\n".join(deck_lines)

    return f"""You are an AI voice presenter giving a short spoken slideshow titled "{TOPIC}".
There are {SLIDE_COUNT} slides. The audience hears you through a speaker and can interrupt you at any time.

THE DECK:
{deck}

HOW TO PRESENT:
- Speak naturally and conversationally, as if presenting live.
- Keep every reply SHORT — 1 to 4 sentences. This is voice, not an essay.
- NEVER use markdown, asterisks, bullet symbols, emojis, or special characters. Speak plain spoken sentences only.
- You begin on Slide 1 (the greeting is already handled). From there, narrate the current slide briefly, then invite questions.

CHANGING SLIDES (IMPORTANT — this is your core skill):
- You have a tool: go_to_slide(slide_number).
- When the user's question or request is best answered by a DIFFERENT slide than the one showing, FIRST call go_to_slide with that slide's number, THEN answer using that slide's speaker notes.
- Say a brief, natural transition as you move, e.g. "Sure, let's look at charging." Keep it to a few words.
- If the user says "next", "go on", or "continue", call go_to_slide with the next number. If they say "go back" or "previous", call go_to_slide with the previous number.
- If the question is about the slide ALREADY showing, just answer — do NOT call the tool.
- Never announce slide numbers out loud (say "let's look at charging", not "going to slide four").
- Stay within slides 1 to {SLIDE_COUNT}.

Always ground your answers in the speaker notes above. If asked something the deck doesn't cover, answer briefly from general knowledge and offer to continue the presentation."""
