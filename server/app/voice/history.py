"""Helpers for compact transcript context snapshots."""

from __future__ import annotations


def recent_turns(turns: list[dict], n: int = 8) -> list[dict]:
    """Return the last `n` transcript turns with the fields the Q&A extractor
    needs to disambiguate short follow-ups later."""
    return [
        {"role": t["role"], "content": t["content"], "slide": t.get("slide")}
        for t in turns[-n:]
    ]
