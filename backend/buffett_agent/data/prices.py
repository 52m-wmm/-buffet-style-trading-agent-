"""Daily prices. One CSV per ticker: date,open,close (adjusted for splits/dividends).

Trading rule used everywhere: information known on day t is traded at the OPEN
of the next trading day. `next_open` is the only way the backtest gets a fill
price, which keeps that rule in one place.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


class PriceStore:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = {t: df.sort_index() for t, df in frames.items()}

    @classmethod
    def from_dir(cls, path: str | Path) -> "PriceStore":
        frames = {}
        for p in Path(path).glob("*.csv"):
            df = pd.read_csv(p, parse_dates=["date"]).set_index("date")
            df.index = df.index.date
            frames[p.stem.upper()] = df[["open", "close"]]
        return cls(frames)

    @classmethod
    def from_yfinance(cls, tickers: list[str], start: date, end: date) -> "PriceStore":
        """Optional: needs `pip install yfinance`. yfinance has no delisted tickers,
        which is a survivorship bias that must be stated in the report."""
        import yfinance as yf  # noqa: PLC0415
        frames = {}
        for t in tickers:
            df = yf.download(t, start=start, end=end, auto_adjust=True, progress=False)
            df = df.rename(columns=str.lower)[["open", "close"]]
            df.index = df.index.date
            frames[t.upper()] = df
        return cls(frames)

    def trading_days(self, ticker: str, start: date, end: date) -> list[date]:
        idx = self.frames[ticker].index
        return [d for d in idx if start <= d <= end]

    def close_on_or_before(self, ticker: str, d: date) -> float | None:
        df = self.frames.get(ticker)
        if df is None:
            return None
        s = df.loc[[x for x in df.index if x <= d], "close"]
        return float(s.iloc[-1]) if len(s) else None

    def next_open(self, ticker: str, d: date) -> tuple[date, float] | None:
        """First open strictly after day d."""
        df = self.frames.get(ticker)
        if df is None:
            return None
        later = [x for x in df.index if x > d]
        if not later:
            return None
        return later[0], float(df.loc[later[0], "open"])
