"""SEC EDGAR client.

Rules enforced here (SEC fair-access policy):
- every request carries a User-Agent with a name and contact email;
- all requests together stay under 10 per second (we use 8 to leave headroom).

Point-in-time rule: every function that returns filings takes `as_of` and only
returns filings whose filing date is strictly before it.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

SEC_WWW = "https://www.sec.gov"
SEC_DATA = "https://data.sec.gov"


@dataclass(frozen=True)
class Filing:
    cik: int
    form: str
    accession: str
    filing_date: date
    report_date: date | None
    primary_document: str

    @property
    def url(self) -> str:
        acc = self.accession.replace("-", "")
        return f"{SEC_WWW}/Archives/edgar/data/{self.cik}/{acc}/{self.primary_document}"


class EdgarClient:
    def __init__(self, user_agent: str | None = None, cache_dir: str | Path = ".cache/edgar",
                 max_rps: float = 8.0):
        ua = user_agent or os.environ.get("SEC_USER_AGENT")
        if not ua or "@" not in ua:
            raise ValueError("SEC requires a User-Agent with a name and email, "
                             "e.g. SEC_USER_AGENT='Jane Doe jane@example.com'")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._min_interval = 1.0 / max_rps
        self._last = 0.0
        self._lock = threading.Lock()

    # -- plumbing -----------------------------------------------------------
    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + self._min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _get(self, url: str, cache_name: str | None = None) -> bytes:
        if cache_name:
            p = self.cache_dir / cache_name
            if p.exists():
                return p.read_bytes()
        for attempt in range(4):
            self._throttle()
            r = self.session.get(url, timeout=60)
            if r.status_code in (429, 503):
                time.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            r.raise_for_status()
            if cache_name:
                p = self.cache_dir / cache_name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(r.content)
            return r.content
        raise RuntimeError(f"SEC refused {url} after retries")

    # -- public API ---------------------------------------------------------
    def ticker_map(self) -> dict[str, int]:
        import json
        raw = json.loads(self._get(f"{SEC_WWW}/files/company_tickers.json", "company_tickers.json"))
        return {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}

    def company_facts(self, cik: int) -> dict:
        import json
        return json.loads(self._get(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik:010d}.json",
                                    f"facts/{cik}.json"))

    def filings(self, cik: int, forms: tuple[str, ...], as_of: date) -> list[Filing]:
        """Filings of the given forms filed strictly before `as_of`, newest first.

        Note: the submissions endpoint lists recent filings; very old ones live in
        extra files under `filings.files`, which this skeleton does not page through.
        """
        import json
        sub = json.loads(self._get(f"{SEC_DATA}/submissions/CIK{cik:010d}.json",
                                   f"submissions/{cik}.json"))
        rec = sub["filings"]["recent"]
        out: list[Filing] = []
        for i, form in enumerate(rec["form"]):
            if form not in forms:
                continue
            fdate = date.fromisoformat(rec["filingDate"][i])
            if fdate >= as_of:
                continue
            rdate = rec["reportDate"][i]
            out.append(Filing(cik=cik, form=form, accession=rec["accessionNumber"][i],
                              filing_date=fdate,
                              report_date=date.fromisoformat(rdate) if rdate else None,
                              primary_document=rec["primaryDocument"][i]))
        out.sort(key=lambda f: f.filing_date, reverse=True)
        return out

    def filing_text(self, filing: Filing) -> str:
        html = self._get(filing.url, f"docs/{filing.cik}/{filing.accession}.html")
        return html_to_text(html.decode("utf-8", errors="replace"))


def html_to_text(html: str) -> str:
    """Strip tags without extra dependencies; good enough for section splitting."""
    import html as htmllib
    import re
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = htmllib.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
