"""Step 1: rule-based screen. No model involved, every rule reports pass/fail and
the number behind it so the result can be audited."""
from __future__ import annotations

import math
import random
from dataclasses import asdict

from .config import ScreenConfig


RULES = ("roe", "debt", "profitability", "fcf", "gross_margin", "roa", "capital")
FINANCIALS = "Financials"   # GICS sector name in data/sp500.csv


def _years_above(series, floor: float, need: int, label: str) -> dict:
    above = int((series.dropna() > floor).sum())
    return {"pass": above >= need, "value": above, "rule": f">= {need} years {label} above {floor:.0%}"}


def screen_company(metrics: dict, cfg: ScreenConfig, sector: str | None = None) -> dict:
    checks: dict[str, dict] = {}
    financial = sector == FINANCIALS

    roe = metrics.get("_roe")
    if roe is None:
        checks["roe"] = {"pass": False, "value": None, "why": "ROE unavailable"}
    else:
        checks["roe"] = _years_above(roe, cfg.roe_min, cfg.roe_years_required, "ROE")

    py = metrics.get("profitable_years")
    checks["profitability"] = {"pass": py is not None and py >= cfg.profitable_years_required,
                               "value": py,
                               "rule": f">= {cfg.profitable_years_required} profitable years"}

    if financial:
        roa = metrics.get("_roa")
        checks["roa"] = (_years_above(roa, cfg.roa_min, cfg.roa_years_required, "ROA")
                         if roa is not None else {"pass": False, "value": None, "why": "total assets unavailable"})
        eta = metrics.get("equity_to_assets")
        checks["capital"] = {"pass": eta is not None and eta >= cfg.equity_to_assets_min,
                             "value": None if eta is None else round(eta, 4),
                             "rule": f"equity / total assets >= {cfg.equity_to_assets_min:.0%}"}
        for rule in ("debt", "fcf", "gross_margin"):
            checks[rule] = {"pass": True, "na": True, "value": None, "why": "not applied to financials"}
        return {"passed": all(c["pass"] for c in checks.values()), "checks": checks, "rules": "financials"}

    d2i = metrics.get("debt_to_income")
    checks["debt"] = {"pass": d2i is not None and d2i <= cfg.debt_to_income_max,
                      "value": None if d2i is None or math.isinf(d2i) else round(d2i, 2),
                      "rule": f"long-term debt <= {cfg.debt_to_income_max}x net income"}
    if metrics.get("debt_assumed_zero"):
        checks["debt"]["why"] = ("no debt on the latest balance sheet and interest under "
                                 "2% of net income; counted as 0")
    elif metrics.get("debt_source"):
        checks["debt"]["why"] = f"latest long-term debt not tagged; used {metrics['debt_source']}"
    elif metrics.get("debt_unknown"):
        checks["debt"]["why"] = "pays interest but no debt balance in the XBRL data; check the 10-K"

    fcf = metrics.get("fcf_cumulative")
    checks["fcf"] = {"pass": fcf is not None and fcf > 0, "value": fcf,
                     "rule": f"cumulative FCF over {cfg.fcf_years} years > 0"}

    gms = metrics.get("gross_margin_std")
    checks["gross_margin"] = {"pass": gms is not None and gms < cfg.gross_margin_std_max,
                              "value": None if gms is None else round(gms, 4),
                              "rule": f"gross margin std < {cfg.gross_margin_std_max:.0%}"}

    return {"passed": all(c["pass"] for c in checks.values()), "checks": checks}


def screen_pool(load_facts, tickers: list[str], as_of, cfg: ScreenConfig, progress=None,
                sectors: dict[str, str] | None = None) -> dict:
    """Step 1 on its own, for a large pool: only XBRL facts are needed, no 10-K text,
    no model. load_facts(ticker) -> companyfacts JSON; failures are skipped, not fatal."""
    from .data.xbrl import fundamentals_as_of, screening_metrics
    results, skipped = {}, {}
    for i, t in enumerate(tickers, 1):
        try:
            m = screening_metrics(fundamentals_as_of(load_facts(t), as_of), cfg.lookback_years,
                                  cfg.fcf_years)
            results[t] = screen_company(m, cfg, (sectors or {}).get(t))
        except Exception as e:
            skipped[t] = f"{type(e).__name__}: {e}"
        if progress:
            progress(i, len(tickers), t)
    fails = {rule: sorted(t for t, r in results.items()
                          if rule in r["checks"] and not r["checks"][rule]["pass"])
             for rule in RULES}
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
