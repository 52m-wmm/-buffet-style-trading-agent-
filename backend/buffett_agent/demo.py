"""Offline demo world: six FICTIONAL companies, synthetic filings, synthetic prices
and a scripted model. Nothing here is real market data; it exists so the whole
pipeline, the UI and the tests run without network access or an API key.

The demo is built to exercise the failure paths, not just the happy path:
- HLNF has a fabricated quote, so the citation check nulls a score and it is skipped;
- NFBR's description gives it away, so its anonymization leak check fails;
- CPLF scores higher when named than anonymized (a 'memory' effect);
- RRSV falls ~25% in August, which triggers the stop-loss rule;
- a later restatement filed after as_of must be ignored (point-in-time).
"""
from __future__ import annotations

import zlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .data.prices import PriceStore
from .sources import CompanyData

AS_OF = date(2026, 7, 1)
MODEL_CUTOFF = date(2026, 6, 30)       # demo assumption
END = date(2026, 10, 23)
TREASURY = 0.042                       # demo assumption, not a market quote
YEARS = list(range(2016, 2026))

PROFILES = {
    # ticker: name, sector, revenue0, growth, gross margin, net margin, equity/NI, debt/NI, ocf/NI, capex/NI, shares(M), target OE yield
    "NFBR": ("Northfield Brands", "Consumer Staples", 10_000, 0.03, 0.55, 0.18, 4.0, 2.2, 1.25, 0.22, 500, 0.075),
    "RRSV": ("Ridgeway Rail Services", "Industrials", 6_000, 0.04, 0.38, 0.15, 5.5, 3.5, 1.40, 0.35, 300, 0.070),
    "CPLF": ("Copperleaf Software", "Technology", 4_000, 0.10, 0.80, 0.25, 3.3, 1.0, 1.30, 0.10, 200, 0.030),
    "HLNF": ("Harbor Lane Foods", "Consumer Staples", 3_000, 0.02, 0.30, 0.08, 6.0, 3.0, 1.20, 0.30, 150, 0.065),
    "VNTR": ("Vantor Mobility", "Consumer Discretionary", 800, 0.30, 0.12, -0.10, 5.0, 6.0, 0.50, 2.00, 400, 0.010),
    "GLMR": ("Glimmer Social", "Communication Services", 1_500, 0.15, 0.60, 0.05, 8.0, 2.0, 1.10, 0.20, 250, 0.040),
}


# ---------------------------------------------------------------- facts ----
def _fact_rows(values: dict[int, float], flow: bool) -> list[dict]:
    rows = []
    for fy, v in values.items():
        end = date(fy, 12, 31)
        row = {"end": end.isoformat(), "val": v, "form": "10-K", "fy": fy, "fp": "FY",
               "filed": date(fy + 1, 2, 20).isoformat()}
        if flow:
            row["start"] = date(fy, 1, 1).isoformat()
        rows.append(row)
    return rows


def _facts(ticker: str) -> dict:
    name, _, rev0, g, gm, nm, eq_mult, debt_mult, ocf_mult, capex_mult, _, _ = PROFILES[ticker]
    rng = np.random.default_rng(zlib.crc32(ticker.encode()))
    rev = {fy: rev0 * (1 + g) ** i * 1e6 for i, fy in enumerate(YEARS)}
    if ticker == "GLMR":   # unstable margins and a loss year
        gms = {fy: gm + rng.normal(0, 0.12) for fy in YEARS}
        nms = {fy: nm + rng.normal(0, 0.08) for fy in YEARS}
    else:
        gms = {fy: gm + rng.normal(0, 0.004) for fy in YEARS}
        nms = {fy: nm + rng.normal(0, 0.004) for fy in YEARS}
    ni = {fy: rev[fy] * nms[fy] for fy in YEARS}
    base_ni = rev0 * 1e6 * abs(nm)
    eq = {fy: base_ni * eq_mult * (1 + g) ** i for i, fy in enumerate(YEARS)}
    debt = {fy: base_ni * debt_mult for fy in YEARS}
    ocf = {fy: max(ni[fy], base_ni * 0.2) * ocf_mult for fy in YEARS}
    capex = {fy: base_ni * capex_mult * (1 + g) ** i for i, fy in enumerate(YEARS)}
    gp = {fy: rev[fy] * gms[fy] for fy in YEARS}
    usgaap = {
        "Revenues": _fact_rows(rev, True),
        "GrossProfit": _fact_rows(gp, True),
        "NetIncomeLoss": _fact_rows(ni, True),
        "StockholdersEquity": _fact_rows(eq, False),
        "LongTermDebtNoncurrent": _fact_rows(debt, False),
        "NetCashProvidedByUsedInOperatingActivities": _fact_rows(ocf, True),
        "PaymentsToAcquirePropertyPlantAndEquipment": _fact_rows(capex, True),
    }
    # A restatement filed AFTER as_of. Point-in-time code must ignore it.
    usgaap["NetIncomeLoss"].append({"end": "2025-12-31", "start": "2025-01-01", "val": -9e12,
                                    "form": "10-K/A", "fy": 2025, "fp": "FY", "filed": "2026-09-15"})
    return {"entityName": name,
            "facts": {"us-gaap": {k: {"units": {"USD": v}} for k, v in usgaap.items()}}}


def owner_earnings_last(ticker: str) -> float:
    a = _facts(ticker)["facts"]["us-gaap"]
    ocf = [r for r in a["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"] if r["fy"] == 2025][0]["val"]
    capex = [r for r in a["PaymentsToAcquirePropertyPlantAndEquipment"]["units"]["USD"] if r["fy"] == 2025][0]["val"]
    return ocf - capex


# ----------------------------------------------------------------- text ----
KEY_SENTENCES = {
    "NFBR": {
        "item_1": ["Our flagship brands have been sold in grocery stores for more than sixty years and are carried in over eighty countries.",
                   "We make money by selling everyday packaged foods and household products at stable prices."],
        "item_1a": ["A sustained rise in commodity input costs could reduce our margins if we cannot raise prices."],
        "item_7": ["We repurchased shares only when we believed the price was below our estimate of intrinsic value.",
                   "Our snack acquisition did not meet our expectations and we wrote down its value this year."],
    },
    "RRSV": {
        "item_1": ["We operate freight rail lines that connect inland manufacturers to three coastal ports.",
                   "Building a competing rail network on our routes would require land and permits that are very difficult to obtain."],
        "item_1a": ["Our results depend on industrial shipping volumes, which fall during economic downturns."],
        "item_7": ["We invest most of our cash flow in track maintenance before considering dividends or buybacks."],
    },
    "CPLF": {
        "item_1": ["We sell policy administration software to mid-sized insurance carriers under multi-year subscriptions.",
                   "Customers rarely switch vendors because migrating policy records is costly and risky."],
        "item_1a": ["Larger software vendors may bundle competing products at lower prices."],
        "item_7": ["We expanded into three new product lines this year through acquisitions."],
    },
    "HLNF": {
        "item_1": ["We produce private label frozen meals for regional grocery chains."],
        "item_1a": ["Our largest customer accounted for a significant portion of revenue and could switch suppliers at short notice."],
        "item_7": ["Gross margin declined as we absorbed higher freight costs that we could not pass on."],
    },
    "VNTR": {
        "item_1": ["We design electric delivery vehicles and have not yet reached profitable production volumes."],
        "item_1a": ["We will need to raise additional capital to continue operations, which may dilute existing shareholders."],
        "item_7": ["Our adjusted metrics exclude several recurring costs that we consider non-operational."],
    },
    "GLMR": {
        "item_1": ["We operate a social app funded mainly by advertising sold to small businesses."],
        "item_1a": ["Changes in mobile platform privacy rules could sharply reduce the effectiveness of our ads."],
        "item_7": ["We changed how we define daily active users during the year."],
    },
}

FILLER = ("This section continues with routine disclosure about operations, facilities, employees, "
          "seasonality and accounting matters for the fiscal year, presented for completeness. ")


def _section(sentences: list[str]) -> str:
    return " ".join(sentences) + "\n\n" + (FILLER * 18)


def filing_text(ticker: str) -> str:
    name = PROFILES[ticker][0]
    ks = KEY_SENTENCES[ticker]
    toc = ("TABLE OF CONTENTS\nItem 1. Business 3\nItem 1A. Risk Factors 9\nItem 1B. Unresolved Staff Comments 20\n"
           "Item 7. Management's Discussion and Analysis 25\nItem 7A. Quantitative Disclosures 40\n"
           "Item 8. Financial Statements 42\n\n")
    return (f"{name} ANNUAL REPORT ON FORM 10-K for fiscal year 2025\n\n" + toc
            + f"Item 1. Business\n{name} ({ticker}). " + _section(ks["item_1"])
            + "\nItem 1A. Risk Factors\n" + _section(ks["item_1a"])
            + "\nItem 1B. Unresolved Staff Comments\nNone.\n"
            + "\nItem 7. Management's Discussion and Analysis\n" + _section(ks["item_7"])
            + "\nItem 7A. Quantitative and Qualitative Disclosures\nNot material.\n"
            + "\nItem 8. Financial Statements\nSee attached.\n")


# --------------------------------------------------------------- scripted model
def _card(scores: dict[str, int | None], quotes: dict[str, tuple[str, str]], flags=()) -> dict:
    out = {}
    for dim, s in scores.items():
        sec, q = quotes[dim]
        out[dim] = {"score": s, "confidence": "medium", "rationale": f"demo rationale for {dim}",
                    "evidence": [{"section": sec, "quote": q}]}
    return {"scores": out, "red_flags": list(flags), "insufficient_evidence": []}


def _q(ticker: str) -> dict[str, tuple[str, str]]:
    k = KEY_SENTENCES[ticker]
    return {"understandable": ("item_1", k["item_1"][0]),
            "moat": ("item_1", k["item_1"][-1]),
            "management": ("item_7", k["item_7"][-1]),
            "financial_quality": ("item_7", k["item_7"][0]),
            "risk": ("item_1a", k["item_1a"][0])}


def scripted_responses() -> tuple[dict, dict]:
    S = lambda u, m, g, f, r: {"understandable": u, "moat": m, "management": g,  # noqa: E731
                               "financial_quality": f, "risk": r}
    resp: dict[str, object] = {}
    # named runs (3 each; small disagreements on purpose)
    resp["NFBR"] = [_card(S(5, 5, 4, 5, 4), _q("NFBR")), _card(S(5, 4, 4, 5, 4), _q("NFBR")),
                    _card(S(5, 5, 4, 5, 4), _q("NFBR"))]
    resp["RRSV"] = [_card(S(5, 4, 4, 4, 4), _q("RRSV"))] * 3
    resp["CPLF"] = [_card(S(4, 5, 4, 5, 4), _q("CPLF")), _card(S(4, 5, 3, 5, 4), _q("CPLF")),
                    _card(S(4, 5, 4, 5, 4), _q("CPLF"))]
    hl = _q("HLNF")
    hl["management"] = ("item_7", "Management has an outstanding record of disciplined capital allocation.")  # fabricated
    resp["HLNF"] = [_card(S(4, 2, 3, 3, 3), hl)] * 3
    resp["VNTR"] = [_card(S(2, 2, 2, 1, 1), _q("VNTR")), _card(S(3, 2, 1, 1, 1), _q("VNTR")),
                    _card(S(2, 2, 2, 1, 1), _q("VNTR"))]
    resp["GLMR"] = [_card(S(4, 2, 2, 2, 2), _q("GLMR"))] * 3
    # anonymized runs: CPLF loses a point on moat once the name is gone (memory effect)
    resp["ANON-NFBR"] = resp["NFBR"]
    resp["ANON-RRSV"] = resp["RRSV"]
    resp["ANON-CPLF"] = [_card(S(4, 4, 4, 4, 4), _q("CPLF"))] * 3
    resp["ANON-HLNF"] = resp["HLNF"]
    resp["ANON-VNTR"] = resp["VNTR"]
    resp["ANON-GLMR"] = resp["GLMR"]
    leak = {"ANON-NFBR": "Northfield Brands", "ANON-CPLF": "some insurance software vendor"}
    return resp, leak


# ---------------------------------------------------------------- prices ----
def _bdays(start: date, end: date) -> list[date]:
    return [d.date() for d in pd.bdate_range(start - timedelta(days=10), end)]


def demo_prices() -> PriceStore:
    days = _bdays(AS_OF, END)
    frames = {}
    for i, (t, prof) in enumerate(list(PROFILES.items()) + [("SPY", None)]):
        rng = np.random.default_rng(100 + i)
        if prof is None:
            p0, drift, vol = 600.0, 0.0003, 0.008
        else:
            shares = prof[10] * 1e6
            p0 = owner_earnings_last(t) / shares / prof[11]
            drift, vol = 0.0004, 0.012
        rets = rng.normal(drift, vol, len(days))
        closes = p0 * np.exp(np.cumsum(rets))
        if t == "RRSV":   # shock: ~25% fall over 10 sessions from Aug 10
            for j, d in enumerate(days):
                if date(2026, 8, 10) <= d <= date(2026, 8, 21):
                    closes[j:] *= 0.972
        opens = np.r_[closes[0], closes[:-1]] * np.exp(rng.normal(0, 0.002, len(days)))
        frames[t] = pd.DataFrame({"open": opens, "close": closes}, index=days)
    return PriceStore(frames)


class DemoSource:
    def __init__(self):
        self.prices = demo_prices()

    def treasury_yield(self, d: date) -> float:
        return TREASURY

    def companies(self, as_of: date) -> list[CompanyData]:
        out = []
        for t, prof in PROFILES.items():
            out.append(CompanyData(ticker=t, names=[prof[0], t], sector=prof[1],
                                   facts=_facts(t), filing_text=filing_text(t),
                                   filing_date=date(2026, 2, 20), shares_outstanding=prof[10] * 1e6))
        return out


def decision_dates() -> list[date]:
    d = date(2026, 7, 6)
    out = []
    while d <= date(2026, 10, 19):
        out.append(d)
        d += timedelta(days=7)
    return out
