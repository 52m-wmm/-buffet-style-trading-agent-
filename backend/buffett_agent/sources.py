"""Where company data comes from. The pipeline only sees `CompanyData`, so the
demo source and the real SEC source run through identical code."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from .data.prices import PriceStore


@dataclass
class CompanyData:
    ticker: str
    names: list[str]                 # company name, brands, products: used for anonymization
    sector: str
    facts: dict                      # SEC companyfacts JSON (or the same shape)
    filing_text: str                 # latest 10-K filed before as_of, as plain text
    filing_date: date
    shares_outstanding: float | None = None
    extra: dict = field(default_factory=dict)


class Source(Protocol):
    prices: PriceStore

    def companies(self, as_of: date) -> list[CompanyData]: ...

    def treasury_yield(self, d: date) -> float: ...


class EdgarSource:
    """Real data: SEC EDGAR for filings and facts, a PriceStore for prices.

    Not exercised in the offline tests (no network there). Run it locally with
    SEC_USER_AGENT set. Treasury yield is passed in as a constant for now; replace
    it with a dated series (e.g. FRED DGS10) before trusting a backtest.
    """

    def __init__(self, tickers: list[str], sectors: dict[str, str], prices: PriceStore,
                 treasury: float, names: dict[str, list[str]] | None = None,
                 user_agent: str | None = None, ciks: dict[str, int] | None = None):
        from .data.edgar import EdgarClient
        self.client = EdgarClient(user_agent)
        self.tickers = tickers
        self.sectors = sectors
        self.prices = prices
        self._treasury = treasury
        self.names = names or {}
        self.ciks = ciks or {}           # known CIKs skip the ticker lookup

    @classmethod
    def sp500(cls, as_of: date, prices: PriceStore, treasury: float, **kw) -> "EdgarSource":
        """Default pool: S&P 500 members as of as_of, with their GICS sectors."""
        from .data.universe import sp500
        members = sp500(as_of)
        return cls([m.ticker for m in members], {m.ticker: m.sector for m in members},
                   prices, treasury, ciks={m.ticker: m.cik for m in members}, **kw)

    def treasury_yield(self, d: date) -> float:
        return self._treasury

    def companies(self, as_of: date) -> list[CompanyData]:
        tmap = self.client.ticker_map()
        out = []
        self.skipped: dict[str, str] = {}   # ticker -> why it was left out
        for t in self.tickers:
            cik = self.ciks.get(t.upper()) or tmap.get(t.upper())
            if cik is None:
                self.skipped[t.upper()] = "no CIK for ticker"
                continue
            try:  # one company's bad data must not stop a 500-name run
                facts = self.client.company_facts(cik)
                tenks = self.client.filings(cik, ("10-K",), as_of)
                if not tenks:
                    self.skipped[t.upper()] = "no 10-K filed before as_of"
                    continue
                f = tenks[0]
                out.append(CompanyData(
                    ticker=t.upper(),
                    names=[facts.get("entityName", ""), t.upper()] + self.names.get(t.upper(), []),
                    sector=self.sectors.get(t.upper(), "unknown"),
                    facts=facts, filing_text=self.client.filing_text(f), filing_date=f.filing_date,
                    shares_outstanding=_shares_as_of(facts, as_of), extra={"cik": cik, "url": f.url}))
            except Exception as e:
                self.skipped[t.upper()] = f"{type(e).__name__}: {e}"
        return out


def _shares_as_of(facts: dict, as_of: date) -> float | None:
    rows = (facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
            .get("units", {}).get("shares", []))
    rows = [r for r in rows if "filed" in r and date.fromisoformat(r["filed"]) < as_of]
    if not rows:
        return None
    return float(max(rows, key=lambda r: r["filed"])["val"])
