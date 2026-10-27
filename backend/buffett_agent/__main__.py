"""Command line.

  python -m buffett_agent demo
  python -m buffett_agent backtest --tickers KO,AAPL --sectors KO=Staples,AAPL=Tech \
      --as-of 2026-07-01 --end 2026-10-23 --model-cutoff 2026-06-30 --prices-dir prices
  (omit --tickers to screen the S&P 500, see data/universe.py)
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from .config import load_config
from .pipeline import Pipeline, run_demo
from .store import Store


def main() -> None:
    p = argparse.ArgumentParser(prog="buffett_agent")
    p.add_argument("--db", default="buffett_agent.db")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="offline run on fictional companies")
    b = sub.add_parser("backtest", help="real SEC data + DeepSeek")
    b.add_argument("--tickers", default="", help="comma-separated; omit to screen the S&P 500")
    b.add_argument("--sectors", default="", help="T=Sector,T2=Sector (S&P 500 uses GICS sectors)")
    b.add_argument("--as-of", type=date.fromisoformat, required=True)
    b.add_argument("--end", type=date.fromisoformat, required=True)
    b.add_argument("--model-cutoff", type=date.fromisoformat, required=True)
    b.add_argument("--prices-dir", default="prices")
    b.add_argument("--treasury", type=float, default=0.042)
    b.add_argument("--repeats", type=int, default=3)
    a = p.parse_args()

    store, cfg = Store(a.db), load_config(a.config)
    if a.cmd == "demo":
        rid = run_demo(store, cfg)
    else:
        from .data.prices import PriceStore
        from .llm.client import DeepSeekClient
        from .sources import EdgarSource
        tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
        sectors = dict(kv.split("=", 1) for kv in a.sectors.split(",") if "=" in kv)
        prices = PriceStore.from_dir(a.prices_dir)
        if tickers:
            src = EdgarSource(tickers, sectors, prices, a.treasury)
        else:
            src = EdgarSource.sp500(a.as_of, prices, a.treasury)
            src.sectors.update(sectors)
            print(f"no --tickers: screening {len(src.tickers)} S&P 500 members as of {a.as_of}")
        dates, d = [], a.as_of + timedelta(days=(7 - a.as_of.weekday()) % 7)
        while d <= a.end:
            dates.append(d)
            d += timedelta(days=7)
        rid = Pipeline(cfg, src, DeepSeekClient(cfg.llm), store, repeats=a.repeats) \
            .run_backtest(a.as_of, dates, a.end, a.model_cutoff)
    print(f"run {rid}")
    for s in store.get_steps(rid):
        print(f"  {s['step']:<9} {s['status']:<7} {s['summary']}")
    check = next(s for s in store.get_steps(rid) if s["step"] == "check")["output"]
    print(json.dumps(check["backtest"], indent=2))


if __name__ == "__main__":
    main()
