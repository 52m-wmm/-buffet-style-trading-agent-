"""Step 5: valuation, code only.

owner earnings ~= operating cash flow - capex (all capex, so conservative)
owner earnings yield = owner earnings / market cap
'price is fair' when yield >= treasury yield + safety margin
"""
from __future__ import annotations


def owner_earnings_yield(owner_earnings: float | None, market_cap: float | None) -> float | None:
    if owner_earnings is None or not market_cap or market_cap <= 0:
        return None
    return owner_earnings / market_cap


def valuation(owner_earnings: float | None, market_cap: float | None,
              treasury_yield: float, safety_margin: float) -> dict:
    y = owner_earnings_yield(owner_earnings, market_cap)
    return {
        "owner_earnings_yield": None if y is None else round(y, 5),
        "hurdle": round(treasury_yield + safety_margin, 5),
        "treasury_yield": treasury_yield,
        "fair_price": y is not None and y >= treasury_yield + safety_margin,
        "overvalued": y is not None and y < treasury_yield,
    }
