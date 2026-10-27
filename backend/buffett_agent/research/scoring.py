"""Steps 3-4: research + scoring. The only place a model is called.

Flow per run: build prompt -> call model -> parse JSON (retry on failure) ->
validate shape with pydantic -> verify every quote -> compute total from the
verified scores. Anything that fails is returned as a failure record, never as a
silent default, and the decision layer skips companies without a valid card.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..llm.client import LLM
from ..llm.prompts import DIMENSIONS, SCORING_SYSTEM, scoring_user_prompt
from .citations import verify_scorecard


class Evidence(BaseModel):
    section: str
    quote: str


class DimensionScore(BaseModel):
    score: int | None = Field(default=None)
    confidence: str = "low"
    rationale: str = ""
    evidence: list[Evidence] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def in_range(cls, v):
        if v is not None and not 1 <= v <= 5:
            raise ValueError("score must be 1-5 or null")
        return v


class ScoreCard(BaseModel):
    scores: dict[str, DimensionScore]
    red_flags: list[str] = Field(default_factory=list)
    insufficient_evidence: list[str] = Field(default_factory=list)


@dataclass
class ScoreResult:
    company_id: str
    ok: bool
    card: dict | None = None
    citations: dict | None = None
    total: float | None = None
    error: str | None = None
    attempts: int = 0
    usage: dict = field(default_factory=dict)


def score_company(llm: LLM, company_id: str, sections: dict[str, str], metrics: dict,
                  max_retries: int = 2) -> ScoreResult:
    user = scoring_user_prompt(company_id, sections, metrics)
    last_err = None
    in_tok = out_tok = 0
    for attempt in range(1, max_retries + 2):
        res = llm.complete_json(SCORING_SYSTEM, user)
        in_tok += res.input_tokens
        out_tok += res.output_tokens
        try:
            card = ScoreCard.model_validate(json.loads(res.text)).model_dump()
        except (json.JSONDecodeError, ValidationError) as e:
            last_err = f"{type(e).__name__}: {str(e)[:200]}"
            continue
        missing = [d for d in DIMENSIONS if d not in card["scores"]]
        if missing:
            last_err = f"missing dimensions {missing}"
            continue
        cites = verify_scorecard(card, sections)
        return ScoreResult(company_id, True, card, cites, total_score(card), attempts=attempt,
                           usage={"model": res.model, "input_tokens": in_tok, "output_tokens": out_tok})
    return ScoreResult(company_id, False, error=last_err, attempts=max_retries + 1,
                       usage={"input_tokens": in_tok, "output_tokens": out_tok})


def total_score(card: dict) -> float | None:
    """Simple average of the five dimensions. If any dimension is null the total is
    null: the decision layer must not trade on a partial picture."""
    vals = [card["scores"][d]["score"] for d in DIMENSIONS]
    if any(v is None for v in vals):
        return None
    return round(statistics.fmean(vals), 3)


def dimension_scores(card: dict) -> dict[str, int | None]:
    return {d: card["scores"][d]["score"] for d in DIMENSIONS}
