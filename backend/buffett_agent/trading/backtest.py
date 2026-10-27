"""Short backtest of the decision layer.

Guards against the biases the JD names:
- Training-data leakage: the run refuses to start before the model's training
  cutoff unless explicitly overridden (and then the report must say so).
- Look-ahead: a scorecard is usable only on days strictly after the filing it
  was built from; orders decided on day d fill at the next day's open.
- Costs: commission + half spread on every fill (see CostConfig).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd

from ..config import Config
from ..data.prices import PriceStore
from .broker import PaperBroker
from .decision import decide
from .risk import apply_risk


@dataclass
class ScoreSnapshot:
    ticker: str
    available_after: date          # filing date of the document scored
    total: float | None
    dims: dict | None


class LookAheadError(RuntimeError):
    pass


def latest_snapshot(snaps: list[ScoreSnapshot], ticker: str, d: date) -> ScoreSnapshot | None:
    usable = [s for s in snaps if s.ticker == ticker and s.available_after < d]
    return max(usable, key=lambda s: s.available_after) if usable else None


def run_backtest(cfg: Config, prices: PriceStore, tickers: list[str], sectors: dict[str, str],
                 snapshots: list[ScoreSnapshot],
                 valuation_fn: Callable[[str, date], dict | None],
                 decision_dates: list[date], end: date, model_cutoff: date,
                 allow_pre_cutoff: bool = False) -> dict:
    if decision_dates and decision_dates[0] <= model_cutoff and not allow_pre_cutoff:
        raise LookAheadError(
            f"backtest starts {decision_dates[0]} but the model was trained on data up to "
            f"{model_cutoff}; results would be inflated. Start after the cutoff.")

    broker = PaperBroker(cash=cfg.initial_cash, costs=cfg.cost)
    log: list[dict] = []
    stopped_on: dict[str, date] = {}
    for d in decision_dates:
        closes = {t: prices.close_on_or_before(t, d) for t in tickers}
        closes = {t: p for t, p in closes.items() if p is not None}
        pv = broker.value(closes)
        decisions = []
        for t in tickers:
            snap = latest_snapshot(snapshots, t, d)
            pos = broker.positions.get(t)
            decisions.append(decide(t, snap.total if snap else None, snap.dims if snap else None,
                                    valuation_fn(t, d), held=pos is not None,
                                    entry_price=pos.entry_price if pos else None,
                                    price=closes.get(t), cfg=cfg.decision,
                                    in_cooldown=t in stopped_on and
                                    (d - stopped_on[t]).days < cfg.decision.reentry_cooldown_days))
        orders, rejected = apply_risk(decisions, pv, broker.cash, broker.holdings_value(closes),
                                      sectors, cfg.risk)
        fills = []
        for o in sorted(orders, key=lambda o: o["side"] != "SELL"):   # sells first
            nxt = prices.next_open(o["ticker"], d)
            if nxt is None:
                rejected.append({"ticker": o["ticker"], "reason": "no_next_open"})
                continue
            fd, px = nxt
            f = (broker.sell(o["ticker"], fd, px, o["rule"]) if o["side"] == "SELL"
                 else broker.buy(o["ticker"], o["notional"], fd, px, o["rule"]))
            if f:
                fills.append(f)
                if f["side"] == "SELL" and f["rule"] == "sell_stop_loss":
                    stopped_on[f["ticker"]] = fd
        log.append({"date": d.isoformat(), "portfolio_value": round(pv, 2),
                    "decisions": [x.to_dict() for x in decisions],
                    "orders": orders, "rejected": rejected, "fills": fills})

    curve = (equity_curve(broker.trades, cfg.initial_cash, prices, tickers, decision_dates[0], end)
             if decision_dates else pd.Series(dtype=float))
    bench = benchmark_curve(prices, cfg.benchmark, decision_dates[0], end) if decision_dates else None
    return {"log": log, "trades": broker.trades, "metrics": metrics(curve, bench, broker.trades),
            "equity_curve": {k.isoformat(): round(v, 2) for k, v in curve.items()}}


def equity_curve(trades: list[dict], initial_cash: float, prices: PriceStore, tickers,
                 start, end) -> pd.Series:
    """Rebuild the daily value by replaying the trade list from the initial cash."""
    days = sorted({d for t in tickers if t in prices.frames for d in prices.trading_days(t, start, end)})
    trades = sorted(trades, key=lambda t: t["date"])
    cash = initial_cash
    shares: dict[str, float] = {}
    out = {}
    i = 0
    for d in days:
        while i < len(trades) and date.fromisoformat(trades[i]["date"]) <= d:
            t = trades[i]
            if t["side"] == "BUY":
                cash -= t["notional"]
                shares[t["ticker"]] = t["shares"]
            else:
                cash += t["notional"] - t["fee"]
                shares.pop(t["ticker"], None)
            i += 1
        val = cash + sum(s * (prices.close_on_or_before(tk, d) or 0) for tk, s in shares.items())
        out[d] = val
    return pd.Series(out, dtype=float)


def benchmark_curve(prices: PriceStore, bench: str, start: date, end: date) -> pd.Series | None:
    if bench not in prices.frames:
        return None
    days = prices.trading_days(bench, start, end)
    return pd.Series({d: prices.close_on_or_before(bench, d) for d in days}, dtype=float)


def max_drawdown(s: pd.Series) -> float | None:
    if s is None or s.empty:
        return None
    return float((s / s.cummax() - 1).min())


def metrics(curve: pd.Series, bench: pd.Series | None, trades: list[dict]) -> dict:
    if curve.empty:
        return {}
    ret = float(curve.iloc[-1] / curve.iloc[0] - 1)
    b = float(bench.iloc[-1] / bench.iloc[0] - 1) if bench is not None and not bench.empty else None
    sells = [t for t in trades if t["side"] == "SELL"]
    by_rule: dict[str, int] = {}
    for t in sells:
        by_rule[t["rule"]] = by_rule.get(t["rule"], 0) + 1
    daily = curve.pct_change().dropna()
    return {
        "total_return": round(ret, 4),
        "benchmark_return": None if b is None else round(b, 4),
        "excess_return": None if b is None else round(ret - b, 4),
        "max_drawdown": round(max_drawdown(curve), 4),
        "benchmark_max_drawdown": None if bench is None else round(max_drawdown(bench), 4),
        "volatility_daily": round(float(daily.std()), 5) if len(daily) > 1 else None,
        "n_trades": len(trades),
        "fees_paid": round(sum(t["fee"] for t in trades), 2),
        "sells_by_rule": by_rule,
        "n_days": int(len(curve)),
        "caveat": "short sample; no statistical significance claimed",
    }
