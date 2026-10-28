"""Step 1: rule-based screen. No model involved, every rule reports pass/fail and
the number behind it so the result can be audited."""
from __future__ import annotations

import math
import random
from dataclasses import asdict

from .config import ScreenConfig


def screen_company(metrics: dict, cfg: ScreenConfig) -> dict:
    checks: dict[str, dict] = {}

    roe = metrics.get("_roe")
    if roe is None:
        checks["roe"] = {"pass": False, "value": None, "why": "ROE unavailable"}
    else:
        above = int((roe.dropna() > cfg.roe_min).sum())
        checks["roe"] = {"pass": above >= cfg.roe_years_required, "value": above,
                         "rule": f">= {cfg.roe_years_required} years above {cfg.roe_min:.0%}"}

    d2i = metrics.get("debt_to_income")
    checks["debt"] = {"pass": d2i is not None and d2i <= cfg.debt_to_income_max,
                      "value": None if d2i is None or math.isinf(d2i) else round(d2i, 2),
                      "rule": f"long-term debt <= {cfg.debt_to_income_max}x net income"}
    if metrics.get("debt_assumed_zero"):
        checks["debt"]["why"] = "no long-term debt reported for the latest year; counted as 0"

    py = metrics.get("profitable_years")
    checks["profitability"] = {"pass": py is not None and py >= cfg.profitable_years_required,
                               "value": py,
                               "rule": f">= {cfg.profitable_years_required} profitable years"}

    fcf = metrics.get("fcf_cumulative")
    checks["fcf"] = {"pass": fcf is not None and fcf > 0, "value": fcf,
                     "rule": f"cumulative FCF over {cfg.fcf_years} years > 0"}

    gms = metrics.get("gross_margin_std")
    checks["gross_margin"] = {"pass": gms is not None and gms < cfg.gross_margin_std_max,
                              "value": None if gms is None else round(gms, 4),
                              "rule": f"gross margin std < {cfg.gross_margin_std_max:.0%}"}

    return {"passed": all(c["pass"] for c in checks.values()), "checks": checks}


def screen_pool(load_facts, tickers: list[str], as_of, cfg: ScreenConfig, progress=None) -> dict:
    """Step 1 on its own, for a large pool: only XBRL facts are needed, no 10-K text,
    no model. load_facts(ticker) -> companyfacts JSON; failures are skipped, not fatal."""
    from .data.xbrl import fundamentals_as_of, screening_metrics
    results, skipped = {}, {}
    for i, t in enumerate(tickers, 1):
        try:
            m = screening_metrics(fundamentals_as_of(load_facts(t), as_of), cfg.lookback_years,
                                  cfg.fcf_years)
            results[t] = screen_company(m, cfg)
        except Exception as e:
            skipped[t] = f"{type(e).__name__}: {e}"
        if progress:
            progress(i, len(tickers), t)
    fails = {rule: sorted(t for t, r in results.items() if not r["checks"][rule]["pass"])
             for rule in ("roe", "debt", "profitability", "fcf", "gross_margin")}
    return {"as_of": str(as_of), "thresholds": asdict(cfg), "pool": len(tickers),
            "screened": len(results), "passed": sorted(t for t, r in results.items() if r["passed"]),
            "failed_by_rule": fails, "skipped": skipped, "results": results}


def split_universe(results: dict[str, dict], n_controls: int = 6, seed: int = 42):
    """Passing companies go to research/trading; a seeded random sample of failing
    ones becomes the control group used only to test the scoring."""
    passed = sorted(t for t, r in results.items() if r["passed"])
    failed = sorted(t for t, r in results.items() if not r["passed"])
    rng = random.Random(seed)
    controls = sorted(rng.sample(failed, min(n_controls, len(failed))))
    return passed, controls
