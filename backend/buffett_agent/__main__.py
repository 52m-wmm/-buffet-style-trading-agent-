"""Command line.

  python -m buffett_agent demo
  python -m buffett_agent screen --as-of 2026-07-01          # step 1 only, S&P 500
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
    s = sub.add_parser("screen", help="step 1 only on real SEC data: no model, no prices")
    s.add_argument("--tickers", default="", help="comma-separated; omit to screen the S&P 500")
    s.add_argument("--as-of", type=date.fromisoformat, required=True)
    s.add_argument("--out", default=None, help="JSON report path (default screen_<as-of>.json)")
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
    if a.cmd == "screen":
        return screen_only(a, cfg)
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


def screen_only(a, cfg) -> None:
    from .data.edgar import EdgarClient
    from .data.universe import sp500
    from .screening import screen_pool
    client = EdgarClient()
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    if tickers:
        tmap = client.ticker_map()
        ciks = {t: tmap[t] for t in tickers if t in tmap}
    else:
        ciks = {m.ticker: m.cik for m in sp500(a.as_of)}
        tickers = list(ciks)
        print(f"no --tickers: screening {len(tickers)} S&P 500 members as of {a.as_of}")

    def load(t):
        if t not in ciks:
            raise KeyError("no CIK for ticker")
        return client.company_facts(ciks[t])

    def progress(i, n, t):
        if i % 25 == 0 or i == n:
            print(f"  {i}/{n} {t}", flush=True)

    rep = screen_pool(load, tickers, a.as_of, cfg.screen, progress)
    out = a.out or f"screen_{a.as_of}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nscreened {rep['screened']} of {rep['pool']}, skipped {len(rep['skipped'])}, "
          f"passed {len(rep['passed'])}")
    for rule, ts in rep["failed_by_rule"].items():
        print(f"  failed {rule:<13} {len(ts)}")
    print("passed:", ", ".join(rep["passed"]) or "none")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
