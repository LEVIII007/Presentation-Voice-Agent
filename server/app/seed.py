"""Seeds the built-in demo deck (Electric Vehicles 101) on first boot, so the
app is presentable before anyone uploads a file. Seeded slides have bullets
instead of rendered images; the viewer renders either."""

from __future__ import annotations

from loguru import logger

from .domain.models import Deck, DeckStatus, Slide, SlideStatus
from .domain.ports import DeckRepo

_TOPIC = "Electric Vehicles 101"

_SLIDES = [
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


async def seed_demo_deck(repo: DeckRepo) -> None:
    if await repo.list_decks():
        return
    deck = Deck(
        id="demo-ev-101",
        title=_TOPIC,
        source_filename="(built-in demo deck)",
        status=DeckStatus.READY,
        slide_count=len(_SLIDES),
    )
    await repo.create_deck(deck)
    await repo.replace_slides(
        deck.id,
        [
            Slide(
                number=i + 1,
                title=s["title"],
                bullets=s["bullets"],
                notes=s["notes"],
                status=SlideStatus.READY,
            )
            for i, s in enumerate(_SLIDES)
        ],
    )
    logger.info("Seeded built-in demo deck 'Electric Vehicles 101'")
