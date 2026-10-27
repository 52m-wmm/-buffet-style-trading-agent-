"""Step 9: reliability checks on the scoring. Pass thresholds are fixed up front;
a failed check is reported, never hidden."""
from __future__ import annotations

import statistics

from .llm.prompts import DIMENSIONS

THRESHOLDS = {
    "consistency_min_share": 0.80,   # share of (company, dimension) with range <= 1
    "citation_min_rate": 0.95,
    "anon_max_mean_gap": 0.5,
}


def consistency(runs_by_company: dict[str, list[dict]]) -> dict:
    """runs_by_company: company -> list of {dimension: score} from repeated runs."""
    cells = ok = 0
    worst = []
    for cid, runs in runs_by_company.items():
        for d in DIMENSIONS:
            vals = [r.get(d) for r in runs if r.get(d) is not None]
            if len(vals) < 2:
                continue
            cells += 1
            rng = max(vals) - min(vals)
            if rng <= 1:
                ok += 1
            else:
                worst.append({"company": cid, "dimension": d, "scores": vals})
    share = ok / cells if cells else None
    return {"share_range_le_1": share, "cells": cells, "unstable": worst,
            "pass": share is not None and share >= THRESHOLDS["consistency_min_share"]}


def citation_rate(citation_reports: list[dict]) -> dict:
    total = sum(r["total"] for r in citation_reports)
    ver = sum(r["verified"] for r in citation_reports)
    rate = ver / total if total else None
    return {"rate": rate, "total": total, "verified": ver,
            "pass": rate is not None and rate >= THRESHOLDS["citation_min_rate"]}


def anonymization_gap(named_totals: dict[str, list[float]], anon_totals: dict[str, list[float]],
                      leaked: dict[str, bool]) -> dict:
    """Compare mean total score named vs anonymized, excluding companies whose
    anonymization leaked (those are listed separately)."""
    gaps = {}
    for cid in named_totals:
        if leaked.get(cid) or cid not in anon_totals:
            continue
        n = [x for x in named_totals[cid] if x is not None]
        a = [x for x in anon_totals[cid] if x is not None]
        if n and a:
            gaps[cid] = round(statistics.fmean(n) - statistics.fmean(a), 3)
    mean_gap = statistics.fmean(abs(g) for g in gaps.values()) if gaps else None
    return {"per_company_gap": gaps, "mean_abs_gap": mean_gap,
            "leaked": sorted(c for c, v in leaked.items() if v),
            "pass": mean_gap is not None and mean_gap <= THRESHOLDS["anon_max_mean_gap"]}


def control_separation(passed_totals: dict[str, float], control_totals: dict[str, float]) -> dict:
    p = [v for v in passed_totals.values() if v is not None]
    c = [v for v in control_totals.values() if v is not None]
    if not p or not c:
        return {"pass": None, "note": "not enough data"}
    return {"passed_mean": round(statistics.fmean(p), 3), "control_mean": round(statistics.fmean(c), 3),
            "pass": statistics.fmean(p) > statistics.fmean(c),
            "note": "small sample; descriptive only"}
