"""Anonymization for the memory test.

We replace the company name, ticker and any extra names (brands, products, CEO)
with neutral tokens. Because business descriptions can still give the company
away, `leak_check` asks the model to guess the company; a correct guess marks
that company as 'anonymization failed' and it is reported separately.
"""
from __future__ import annotations

import json
import re

from ..llm.client import LLM
from ..llm.prompts import LEAK_CHECK_SYSTEM


def anonymize(text: str, names: list[str], token: str = "THE COMPANY") -> str:
    # longest names first so "Acme Widgets Inc" is replaced before "Acme"
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", token, text, flags=re.I)
    return text


def anonymize_sections(sections: dict[str, str], names: list[str]) -> dict[str, str]:
    return {k: anonymize(v, names) for k, v in sections.items()}


def leak_check(llm: LLM, company_id: str, anonymized_excerpt: str, true_names: list[str]) -> dict:
    res = llm.complete_json(LEAK_CHECK_SYSTEM, f"COMPANY_ID: {company_id}\n" + anonymized_excerpt[:8000])
    try:
        guess = (json.loads(res.text).get("guess") or "").lower()
    except json.JSONDecodeError:
        return {"guess": None, "leaked": None, "error": "unparseable"}
    leaked = any(n.lower() in guess for n in true_names if n)
    return {"guess": guess or None, "leaked": leaked}
