"""Step 7: risk checks between decision and execution.

Sells are always allowed (they reduce risk). Buys are sized to the position cap
and rejected if they would break a limit. Every rejection is logged with a reason.
"""
from __future__ import annotations

from ..config import RiskConfig
from .decision import Decision


def apply_risk(decisions: list[Decision], portfolio_value: float, cash: float,
               holdings_value: dict[str, float], sectors: dict[str, str],
               cfg: RiskConfig) -> tuple[list[dict], list[dict]]:
    """Return (approved orders, rejections). An order is {ticker, side, notional}."""
    approved: list[dict] = []
    rejected: list[dict] = []

    if not cfg.trading_enabled:
        return [], [{"ticker": d.ticker, "reason": "kill_switch_off"}
                    for d in decisions if d.action in ("BUY", "SELL")]

    for d in decisions:
        if d.action == "SELL":
            approved.append({"ticker": d.ticker, "side": "SELL", "notional": None, "rule": d.rule})

    sector_value: dict[str, float] = {}
    for t, v in holdings_value.items():
        sector_value[sectors.get(t, "unknown")] = sector_value.get(sectors.get(t, "unknown"), 0) + v
    sold = {o["ticker"] for o in approved}
    for o in approved:  # value freed by sells counts toward sector room and cash
        sec = sectors.get(o["ticker"], "unknown")
        sector_value[sec] = sector_value.get(sec, 0) - holdings_value.get(o["ticker"], 0)
        cash += holdings_value.get(o["ticker"], 0)

    target = cfg.max_position_weight * portfolio_value
    new_count = 0
    for d in sorted((d for d in decisions if d.action == "BUY"), key=lambda d: d.ticker):
        sec = sectors.get(d.ticker, "unknown")
        if d.ticker in holdings_value and d.ticker not in sold:
            rejected.append({"ticker": d.ticker, "reason": "already_held"})
            continue
        if new_count >= cfg.max_new_positions_per_step:
            rejected.append({"ticker": d.ticker, "reason": "max_new_positions_per_step"})
            continue
        room_sector = cfg.max_sector_weight * portfolio_value - sector_value.get(sec, 0)
        notional = min(target, room_sector, cash)
        if notional < 0.25 * target:   # too small to be meaningful
            reason = ("sector_limit" if room_sector < 0.25 * target else "insufficient_cash")
            rejected.append({"ticker": d.ticker, "reason": reason})
            continue
        approved.append({"ticker": d.ticker, "side": "BUY", "notional": round(notional, 2),
                         "rule": d.rule})
        cash -= notional
        sector_value[sec] = sector_value.get(sec, 0) + notional
        new_count += 1
    return approved, rejected
