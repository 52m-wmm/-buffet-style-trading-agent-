"""Split a 10-K's plain text into the items the agent reads.

10-K layouts vary, and the table of contents repeats every heading, so for each
item we take the LAST heading match that is followed by a substantial body.
Anything not found is reported as missing rather than guessed.
"""
from __future__ import annotations

import re

ITEMS = {
    "item_1": (r"item\s*1\.?\s*business", r"item\s*1a\.?\s*risk\s*factors"),
    "item_1a": (r"item\s*1a\.?\s*risk\s*factors", r"item\s*1b\.?|item\s*2\.?\s*properties"),
    "item_7": (r"item\s*7\.?\s*management.{0,5}s\s*discussion",
               r"item\s*7a\.?|item\s*8\.?\s*financial\s*statements"),
}
MIN_BODY_CHARS = 2000


def split_10k(text: str, max_chars_per_item: int = 120_000) -> dict:
    out: dict[str, str] = {}
    missing: list[str] = []
    for key, (start_pat, end_pat) in ITEMS.items():
        best = None
        for m in re.finditer(start_pat, text, flags=re.I):
            end = re.search(end_pat, text[m.end():], flags=re.I)
            body = text[m.end(): m.end() + end.start()] if end else text[m.end():]
            if len(body.strip()) >= MIN_BODY_CHARS:
                best = body.strip()      # keep the last substantial match
        if best is None:
            missing.append(key)
        else:
            out[key] = best[:max_chars_per_item]
    return {"sections": out, "missing": missing}
