"""Default stock pool: S&P 500 constituents.

sp500.csv is a snapshot of the current members (source: github.com/datasets/
s-and-p-500-companies, taken 2026-10-27), with GICS sector, SEC CIK and the date
each company joined the index. Tickers use the SEC/yfinance form (BRK-B, not BRK.B).

Point in time, partly: members that joined after as_of are dropped, but companies
that left the index before today are not in the file at all. That survivorship
bias must be stated in the report; fix it with a historical membership list.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SP500_CSV = Path(__file__).with_name("sp500.csv")
SNAPSHOT_DATE = date(2026, 10, 27)


@dataclass(frozen=True)
class Member:
    ticker: str
    name: str
    sector: str
    cik: int
    date_added: date


def sp500(as_of: date | None = None) -> list[Member]:
    """Current S&P 500 members; with as_of, only those already in the index then."""
    with SP500_CSV.open(encoding="utf-8") as f:
        members = [Member(r["ticker"], r["name"], r["sector"], int(r["cik"]),
                          date.fromisoformat(r["date_added"])) for r in csv.DictReader(f)]
    if as_of is not None:
        members = [m for m in members if m.date_added <= as_of]
    return members
