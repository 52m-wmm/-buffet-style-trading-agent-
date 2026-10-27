"""Point-in-time financial metrics from SEC XBRL companyfacts.

Two traps this module handles:
1. Companies tag the same concept differently, so each metric has a list of
   candidate tags (TAGS). The first tag with data wins, and the chosen tag is
   reported back so a human can audit it.
2. Restatements: a later 10-K can restate an earlier year. For point-in-time
   correctness we only use facts whose `filed` date is before `as_of`, and for
   each fiscal year keep the most recently filed value among those.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

TAGS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "SalesRevenueNet"],
    # Fallbacks after the first entry were chosen from what the S&P 500 actually files
    # (2026-10-27 screen); each one means the same thing as the tag before it.
    "net_income": ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
                   "ProfitLoss"],   # ProfitLoss includes minority interests
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
                "LongTermNotesPayable", "OtherLongTermDebtNoncurrent"],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold",
                        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    # Not CostsAndExpenses: that is total cost, which would give operating margin.
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquireOtherPropertyPlantAndEquipment", "PaymentsForCapitalImprovements"],
}
FLOW = {"revenue", "net_income", "gross_profit", "cost_of_revenue", "ocf", "capex"}


@dataclass
class Fundamentals:
    annual: pd.DataFrame                      # index = fiscal year end, columns = metrics
    tags_used: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def _annual_series(facts: dict, tag: str, as_of: date, flow: bool) -> pd.Series:
    units = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {})
    rows = units.get("USD", [])
    recs = []
    for r in rows:
        if r.get("form") not in ("10-K", "10-K/A") or "filed" not in r:
            continue
        if date.fromisoformat(r["filed"]) >= as_of:
            continue  # not yet public at as_of
        end = date.fromisoformat(r["end"])
        if flow:
            if "start" not in r:
                continue
            days = (end - date.fromisoformat(r["start"])).days
            if not 350 <= days <= 380:   # full fiscal year only, not quarters
                continue
        recs.append((end, date.fromisoformat(r["filed"]), float(r["val"])))
    if not recs:
        return pd.Series(dtype=float)
    df = pd.DataFrame(recs, columns=["end", "filed", "val"])
    df = df.sort_values(["end", "filed"]).groupby("end").tail(1)  # latest filing per year
    return df.set_index("end")["val"].sort_index()


def fundamentals_as_of(facts: dict, as_of: date) -> Fundamentals:
    cols: dict[str, pd.Series] = {}
    used: dict[str, str] = {}
    missing: list[str] = []
    for metric, candidates in TAGS.items():
        # Companies switch tags over time (KO moved off LongTermDebtNoncurrent in 2024),
        # so fill each year from the first candidate that has it, not the first tag only.
        merged, tags = None, []
        for tag in candidates:
            s = _annual_series(facts, tag, as_of, metric in FLOW)
            if s.empty:
                continue
            if merged is None:
                merged, tags = s, [tag]
            elif len(s.index.difference(merged.index)):
                merged, tags = merged.combine_first(s), tags + [tag]
        if merged is None:
            missing.append(metric)
        else:
            cols[metric] = merged
            used[metric] = " + ".join(tags)
    annual = pd.DataFrame(cols).sort_index()
    if {"revenue", "cost_of_revenue"} <= set(annual):
        # Per year, not only when GrossProfit is absent: COST stopped tagging it in 2019.
        derived = annual["revenue"] - annual["cost_of_revenue"]
        if "gross_profit" not in annual:
            annual["gross_profit"] = derived
            used["gross_profit"] = "derived: revenue - cost_of_revenue"
        elif annual["gross_profit"].isna().any() and derived[annual["gross_profit"].isna()].notna().any():
            annual["gross_profit"] = annual["gross_profit"].fillna(derived)
            used["gross_profit"] += " + derived: revenue - cost_of_revenue"
        if "gross_profit" in missing:
            missing.remove("gross_profit")
    return Fundamentals(annual=annual, tags_used=used, missing=missing)


def screening_metrics(f: Fundamentals, lookback: int = 10, fcf_years: int = 5) -> dict:
    """Numbers the screen and the financial-quality score use. Computed by code only."""
    a = f.annual.tail(lookback)
    out: dict = {"years_available": int(len(a)), "tags_used": f.tags_used,
                 "missing": f.missing}
    if a.empty:
        return out
    if {"net_income", "equity"} <= set(a):
        eq = a["equity"].where(a["equity"] > 0)   # negative equity -> ROE undefined
        roe = a["net_income"] / eq
        out["roe_by_year"] = {str(k): (None if pd.isna(v) else round(float(v), 4))
                              for k, v in roe.items()}
        out["roe_years_above"] = None  # filled by screen with its threshold
        out["_roe"] = roe
    if "net_income" in a:
        out["profitable_years"] = int((a["net_income"] > 0).sum())
        last_ni = a["net_income"].iloc[-1]
        if "lt_debt" in a and pd.notna(a["lt_debt"].iloc[-1]):
            out["debt_to_income"] = (float(a["lt_debt"].iloc[-1] / last_ni)
                                     if last_ni > 0 else float("inf"))
        else:
            out["debt_to_income"] = 0.0 if "lt_debt" in f.missing else None
    if {"ocf", "capex"} <= set(a):
        fcf = (a["ocf"] - a["capex"]).tail(fcf_years)
        out["fcf_cumulative"] = float(fcf.sum())
        out["owner_earnings_last"] = float(fcf.iloc[-1])
    if {"gross_profit", "revenue"} <= set(a):
        gm = (a["gross_profit"] / a["revenue"]).replace([np.inf, -np.inf], np.nan).dropna()
        out["gross_margin_std"] = float(gm.std()) if len(gm) > 1 else None
    return out
