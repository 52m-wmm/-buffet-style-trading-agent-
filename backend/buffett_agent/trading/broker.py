"""Step 8: paper broker. Long only, no leverage, costs charged on every fill."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..config import CostConfig


@dataclass
class Position:
    shares: float
    entry_price: float
    entry_date: date


@dataclass
class PaperBroker:
    cash: float
    costs: CostConfig
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)

    @property
    def cost_rate(self) -> float:
        return (self.costs.commission_bps + self.costs.spread_bps) / 10_000

    def buy(self, ticker: str, notional: float, fill_date: date, price: float, rule: str = "") -> dict:
        notional = min(notional, self.cash)
        fee = notional * self.cost_rate
        shares = (notional - fee) / price
        self.cash -= notional
        self.positions[ticker] = Position(shares, price, fill_date)
        t = {"date": fill_date.isoformat(), "ticker": ticker, "side": "BUY", "price": price,
             "shares": round(shares, 6), "notional": round(notional, 2), "fee": round(fee, 2),
             "rule": rule}
        self.trades.append(t)
        return t

    def sell(self, ticker: str, fill_date: date, price: float, rule: str = "") -> dict | None:
        pos = self.positions.pop(ticker, None)
        if pos is None:
            return None
        gross = pos.shares * price
        fee = gross * self.cost_rate
        self.cash += gross - fee
        t = {"date": fill_date.isoformat(), "ticker": ticker, "side": "SELL", "price": price,
             "shares": round(pos.shares, 6), "notional": round(gross, 2), "fee": round(fee, 2),
             "rule": rule, "pnl_pct": round(price / pos.entry_price - 1, 4)}
        self.trades.append(t)
        return t

    def holdings_value(self, prices: dict[str, float]) -> dict[str, float]:
        return {t: p.shares * prices[t] for t, p in self.positions.items() if t in prices}

    def value(self, prices: dict[str, float]) -> float:
        return self.cash + sum(self.holdings_value(prices).values())
