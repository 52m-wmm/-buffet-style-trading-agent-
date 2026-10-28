"""All thresholds live here and are fixed before a run.

Changing a threshold after seeing results is the easiest way to overfit, so the
config is written into every run record (see store.py) and can be diffed later.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ScreenConfig:
    roe_min: float = 0.15            # ROE threshold per year
    roe_years_required: int = 8      # out of the last 10 fiscal years
    debt_to_income_max: float = 4.0  # long-term debt <= 4x net income
    profitable_years_required: int = 9
    fcf_years: int = 5               # cumulative FCF over this many years must be > 0
    gross_margin_std_max: float = 0.05
    lookback_years: int = 10
    # Financials (GICS sector): deposits and float are the business, so debt, FCF and gross
    # margin say nothing. Buffett judges banks by return on assets and by how much capital
    # stands behind them; these two rules replace the three that don't apply.
    roa_min: float = 0.01                 # net income / total assets per year
    roa_years_required: int = 8
    equity_to_assets_min: float = 0.08    # latest year


@dataclass(frozen=True)
class DecisionConfig:
    buy_total_min: float = 4.0
    buy_dimension_floor: int = 2          # no dimension below this to buy
    sell_total_below: float = 3.5
    sell_dimension_at: int = 1            # any dimension at this level -> sell
    safety_margin: float = 0.02           # owner-earnings yield must beat treasury + this
    stop_loss: float | None = 0.20        # None disables the price stop
    reentry_cooldown_days: int = 90       # after a stop-loss, no re-buy for this long
    # Without the cooldown the demo showed a whipsaw: stopped out, then re-bought a
    # week later because the lower price made the valuation look better.


@dataclass(frozen=True)
class RiskConfig:
    max_position_weight: float = 0.10
    max_sector_weight: float = 0.30
    max_new_positions_per_step: int = 3
    trading_enabled: bool = True          # manual kill switch


@dataclass(frozen=True)
class CostConfig:
    commission_bps: float = 1.0           # per side, on traded notional
    spread_bps: float = 5.0               # half-spread paid per side


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    temperature: float = 0.0
    max_retries: int = 2
    timeout_s: float = 120.0


@dataclass(frozen=True)
class Config:
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    decision: DecisionConfig = field(default_factory=DecisionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    initial_cash: float = 100_000.0
    benchmark: str = "SPY"

    def to_dict(self) -> dict:
        return asdict(self)


def with_screen(cfg: Config, overrides: dict) -> Config:
    """Copy of cfg with some screen thresholds replaced (e.g. from the UI form).
    The run record stores the merged config, so every run stays reproducible."""
    known = {f.name for f in fields(ScreenConfig)}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(f"unknown screen thresholds: {', '.join(sorted(unknown))}")
    s = replace(cfg.screen, **{k: v for k, v in overrides.items() if v is not None})
    for name in ("roe_years_required", "profitable_years_required", "fcf_years",
                 "roa_years_required"):
        if getattr(s, name) > s.lookback_years:
            raise ValueError(f"{name} ({getattr(s, name)}) > lookback_years ({s.lookback_years})")
    return replace(cfg, screen=s)


def load_config(path: str | Path | None = None) -> Config:
    """Load overrides from a YAML file; missing keys keep their defaults."""
    if path is None or not Path(path).exists():
        return Config()
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Config(
        screen=ScreenConfig(**raw.get("screen", {})),
        decision=DecisionConfig(**raw.get("decision", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        cost=CostConfig(**raw.get("cost", {})),
        llm=LLMConfig(**raw.get("llm", {})),
        initial_cash=raw.get("initial_cash", 100_000.0),
        benchmark=raw.get("benchmark", "SPY"),
    )
