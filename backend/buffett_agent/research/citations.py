"""Check that every quote the model gives actually appears in the source text."""
from __future__ import annotations

import re
import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def verify_quote(quote: str, source: str) -> bool:
    """Exact match after whitespace/quote-style normalization. Deliberately strict:
    a paraphrase presented as a quote counts as a failure."""
    q = _norm(quote)
    return bool(q) and q in _norm(source)


def verify_scorecard(card: dict, sections: dict[str, str]) -> dict:
    """Mark each evidence item verified/unverified; a score with no verified
    evidence is nulled so it cannot reach the decision layer."""
    total = verified = 0
    failures = []
    for dim, entry in (card.get("scores") or {}).items():
        ok_any = False
        for ev in entry.get("evidence") or []:
            total += 1
            src = sections.get(ev.get("section", ""), "") or " ".join(sections.values())
            ok = verify_quote(ev.get("quote", ""), src)
            ev["verified"] = ok
            if ok:
                verified += 1
                ok_any = True
            else:
                failures.append({"dimension": dim, "quote": ev.get("quote", "")[:200]})
        if entry.get("score") is not None and not ok_any:
            entry["score_before_check"] = entry["score"]
            entry["score"] = None
            entry["nulled_reason"] = "no verified evidence"
    return {"total": total, "verified": verified,
            "rate": (verified / total) if total else None, "failures": failures}
