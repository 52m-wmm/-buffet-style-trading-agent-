"""Step 6: turn scores + valuation into BUY / HOLD / SELL with fixed rules.

The model never places orders. Each decision carries the rule that fired so the
report can separate 'sold on fundamentals' from 'sold on stop loss'.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..config import DecisionConfig


@dataclass
class Decision:
    ticker: str
    action: str          # BUY | HOLD | SELL | SKIP
    rule: str
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def decide(ticker: str, total: float | None, dims: dict[str, int | None] | None,
           val: dict | None, held: bool, entry_price: float | None,
           price: float | None, cfg: DecisionConfig, in_cooldown: bool = False) -> Decision:
    if total is None or dims is None:
        return Decision(ticker, "HOLD" if held else "SKIP", "no_valid_scorecard",
                        "missing or failed scorecard; no new trades")

    if held:
        if any(v is not None and v <= cfg.sell_dimension_at for v in dims.values()):
            weak = [k for k, v in dims.items() if v is not None and v <= cfg.sell_dimension_at]
            return Decision(ticker, "SELL", "sell_fundamental_dimension", f"weak: {weak}")
        if total < cfg.sell_total_below:
            return Decision(ticker, "SELL", "sell_fundamental_total",
                            f"total {total} < {cfg.sell_total_below}")
        if val and val.get("overvalued"):
            return Decision(ticker, "SELL", "sell_valuation",
                            f"yield {val['owner_earnings_yield']} < treasury {val['treasury_yield']}")
        if (cfg.stop_loss is not None and entry_price and price
                and price <= entry_price * (1 - cfg.stop_loss)):
            return Decision(ticker, "SELL", "sell_stop_loss",
                            f"price {price:.2f} <= entry {entry_price:.2f} x {1 - cfg.stop_loss:.2f}")
        return Decision(ticker, "HOLD", "hold_no_sell_condition")

    if in_cooldown:
        return Decision(ticker, "SKIP", "skip_cooldown_after_stop",
                        f"stopped out within the last {cfg.reentry_cooldown_days} days")
    floor_ok = all(v is not None and v >= cfg.buy_dimension_floor for v in dims.values())
    if total >= cfg.buy_total_min and floor_ok and val and val.get("fair_price"):
        return Decision(ticker, "BUY", "buy_quality_and_price",
                        f"total {total}, yield {val['owner_earnings_yield']} >= {val['hurdle']}")
    reasons = []
    if total < cfg.buy_total_min:
        reasons.append(f"total {total} < {cfg.buy_total_min}")
    if not floor_ok:
        reasons.append("a dimension below floor")
    if not (val and val.get("fair_price")):
        reasons.append("price not fair")
    return Decision(ticker, "SKIP", "skip_not_qualified", "; ".join(reasons))
