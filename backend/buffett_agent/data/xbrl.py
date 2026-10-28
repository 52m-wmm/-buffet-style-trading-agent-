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
                "LongTermNotesPayable", "OtherLongTermDebtNoncurrent",
                # Unclassified balance sheets (banks, GM, APD) have no noncurrent split; these
                # include current maturities or short-term debt, so they can only overstate.
                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                "UnsecuredLongTermDebt", "DebtLongtermAndShorttermCombinedAmount"],
    "assets": ["Assets"],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold",
                        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    # Not CostsAndExpenses: that is total cost, which would give operating margin.
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquireOtherPropertyPlantAndEquipment", "PaymentsForCapitalImprovements"],
    "interest": ["InterestExpense", "InterestExpenseDebt", "InterestExpenseNonoperating",
                 "InterestPaidNet", "InterestPaid"],
}
FLOW = {"revenue", "net_income", "gross_profit", "cost_of_revenue", "ocf", "capex", "interest"}
AUX = {"assets", "interest"}

# Used only when no lt_debt tag covers the latest year: any of these on the balance sheet
# means the company does owe money, so its debt is not zero. Asset-side and schedule tags
# (AvailableForSaleSecuritiesDebt..., LongTermDebtMaturities...) are excluded by name.
_DEBT_WORDS = ("Debt", "Borrowings", "NotesPayable", "SeniorNotes", "ConvertibleNotes",
               "Debentures", "CommercialPaper", "LineOfCredit")
_NOT_DEBT = ("OtherThan", "Securities", "Investment", "Receivable", "Maturities", "FaceAmount", "FairValue",
             "Interest", "Rate", "Percentage", "Capacity", "Maximum", "Remaining", "Unamortized",
             "Discount", "Premium", "Cost", "Fee", "Covenant", "Weighted", "Increase", "Decrease")
# Interest at or below this share of net income: even at a 2% rate the debt behind it is
# under 1x net income, well inside debt_to_income_max, so treating it as 0 cannot flip a pass.
IMMATERIAL_INTEREST = 0.02


@dataclass
class Fundamentals:
    annual: pd.DataFrame                      # index = fiscal year end, columns = metrics
    tags_used: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    other_debt: dict[str, float] = field(default_factory=dict)   # see _debt_like_at


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


def _debt_like_at(facts: dict, end, as_of: date) -> dict[str, float]:
    """Debt-looking balances (not in TAGS) reported for fiscal year end `end`."""
    found = {}
    for tag, v in facts.get("facts", {}).get("us-gaap", {}).items():
        if not any(w in tag for w in _DEBT_WORDS) or any(w in tag for w in _NOT_DEBT):
            continue
        vals = [float(r["val"]) for r in v.get("units", {}).get("USD", [])
                if r.get("form") in ("10-K", "10-K/A") and "start" not in r and "filed" in r
                and date.fromisoformat(r["filed"]) < as_of and date.fromisoformat(r["end"]) == end]
        if vals and max(vals) > 0:
            found[tag] = max(vals)
    return found


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
    # Auxiliary metrics must not add fiscal years of their own: a restated opening balance
    # (TJX Assets at 2019-02-03) would otherwise become an empty year inside the lookback.
    aux = {m: cols.pop(m) for m in AUX if m in cols}
    annual = pd.DataFrame(cols).sort_index()
    for m, s in aux.items():
        annual[m] = s.reindex(annual.index)
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
    other = {}
    if not annual.empty and ("lt_debt" not in annual or pd.isna(annual["lt_debt"].iloc[-1])):
        other = _debt_like_at(facts, annual.index[-1], as_of)
    return Fundamentals(annual=annual, tags_used=used, missing=missing, other_debt=other)


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
        interest = a["interest"].iloc[-1] if "interest" in a else np.nan
        if "lt_debt" in a and pd.notna(a["lt_debt"].iloc[-1]):
            debt = float(a["lt_debt"].iloc[-1])
        elif f.other_debt:
            # Debt is on the balance sheet under a tag we don't map: take the largest so
            # the estimate can only overstate it.
            tag = max(f.other_debt, key=f.other_debt.get)
            debt, out["debt_source"] = f.other_debt[tag], tag
        elif last_ni > 0 and (pd.isna(interest) or interest <= IMMATERIAL_INTEREST * last_ni):
            debt, out["debt_assumed_zero"] = 0.0, True   # MNST, ANET: nothing owed to speak of
        else:
            # Paying real interest but no debt balance in the data (F and AES only report it
            # by segment, which companyfacts drops): unknown, so the debt rule fails.
            debt, out["debt_unknown"] = None, True
            out["interest_last"] = None if pd.isna(interest) else float(interest)
        if debt is not None:
            out["debt_to_income"] = (float(debt / last_ni) if last_ni > 0
                                     else 0.0 if debt == 0 else float("inf"))
    if {"net_income", "assets"} <= set(a):
        roa = a["net_income"] / a["assets"].where(a["assets"] > 0)
        out["roa_by_year"] = {str(k): (None if pd.isna(v) else round(float(v), 4))
                              for k, v in roa.items()}
        out["_roa"] = roa
        if "equity" in a and pd.notna(a["assets"].iloc[-1]) and pd.notna(a["equity"].iloc[-1]):
            out["equity_to_assets"] = float(a["equity"].iloc[-1] / a["assets"].iloc[-1])
    if {"ocf", "capex"} <= set(a):
        fcf = (a["ocf"] - a["capex"]).tail(fcf_years)
        out["fcf_cumulative"] = float(fcf.sum())
        out["owner_earnings_last"] = float(fcf.iloc[-1])
    if {"gross_profit", "revenue"} <= set(a):
        gm = (a["gross_profit"] / a["revenue"]).replace([np.inf, -np.inf], np.nan).dropna()
        out["gross_margin_std"] = float(gm.std()) if len(gm) > 1 else None
    return out
