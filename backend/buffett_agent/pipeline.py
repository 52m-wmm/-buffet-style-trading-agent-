"""The 9-step workflow. Each step records its inputs/outputs so a person (and the
UI canvas) can inspect exactly what happened.

Only steps 3 and 4 call a model. Everything else is deterministic code.
"""
from __future__ import annotations

import statistics
import traceback
from datetime import date, datetime, timezone

from . import checks
from .config import Config
from .data.sections import split_10k
from .data.xbrl import fundamentals_as_of, screening_metrics
from .llm.client import LLM
from .llm.prompts import DIMENSIONS
from .research.anonymize import anonymize_sections, leak_check
from .research.scoring import dimension_scores, score_company, total_score
from .screening import screen_company, split_universe
from .sources import CompanyData, Source
from .store import Store
from .trading.backtest import ScoreSnapshot, run_backtest
from .trading.valuation import valuation

STEPS = [
    {"id": "screen", "label": "1 规则初筛", "kind": "code",
     "desc": "按ROE、负债、盈利稳定、自由现金流、毛利率过滤"},
    {"id": "data", "label": "2 数据准备", "kind": "code",
     "desc": "只取决策日之前提交的10-K；切段；生成匿名版本"},
    {"id": "research", "label": "3 研究", "kind": "model",
     "desc": "回测模式只读文件；往前跑模式可联网，搜索设日期上限"},
    {"id": "score", "label": "4 打分", "kind": "model",
     "desc": "五个维度1到5分，引用核对后才算数；实名和匿名各跑多次"},
    {"id": "value", "label": "5 估值", "kind": "code",
     "desc": "所有者盈余收益率对比美债收益率加安全边际"},
    {"id": "decide", "label": "6 决策", "kind": "code",
     "desc": "写死的规则：买入、持有、卖出（基本面/估值/止损）"},
    {"id": "risk", "label": "7 风控", "kind": "code",
     "desc": "单只仓位、行业集中度、每步新开仓上限；超限拒绝"},
    {"id": "execute", "label": "8 模拟执行", "kind": "code",
     "desc": "下一个开盘价成交，扣佣金和价差，全部留痕"},
    {"id": "check", "label": "9 检验", "kind": "code",
     "desc": "一致性、引用、匿名差异、对照组、短回测对比基准"},
]
EDGES = [(STEPS[i]["id"], STEPS[i + 1]["id"]) for i in range(len(STEPS) - 1)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _median_dims(runs: list[dict]) -> dict[str, int | None]:
    out = {}
    for d in DIMENSIONS:
        vals = [r[d] for r in runs if r.get(d) is not None]
        out[d] = int(statistics.median_low(vals)) if vals else None
    return out


class Pipeline:
    def __init__(self, cfg: Config, source: Source, llm: LLM, store: Store,
                 repeats: int = 3, run_anonymized: bool = True):
        self.cfg, self.source, self.llm, self.store = cfg, source, llm, store
        self.repeats = repeats
        self.run_anonymized = run_anonymized

    def _step(self, rid: str, step: str, fn):
        started = _now()
        try:
            summary, output = fn()
            self.store.save_step(rid, step, "ok", started, summary, output)
            return output
        except Exception as e:  # recorded, then re-raised: a failed step stops the run
            self.store.save_step(rid, step, "failed", started, f"{type(e).__name__}: {e}",
                                 {"traceback": traceback.format_exc()})
            raise

    def run_backtest(self, as_of: date, decision_dates: list[date], end: date,
                     model_cutoff: date, mode: str = "backtest") -> str:
        cfg = self.cfg
        rid = self.store.new_run(mode, cfg.to_dict() | {"as_of": as_of.isoformat(),
                                                        "model_cutoff": model_cutoff.isoformat()})
        try:
            companies: dict[str, CompanyData] = {c.ticker: c for c in self.source.companies(as_of)}

            # 1 screen ------------------------------------------------------
            def screen():
                results, metrics = {}, {}
                for t, c in companies.items():
                    f = fundamentals_as_of(c.facts, as_of)
                    m = screening_metrics(f, cfg.screen.lookback_years, cfg.screen.fcf_years)
                    results[t] = screen_company(m, cfg.screen)
                    m.pop("_roe", None)
                    metrics[t] = m
                passed, controls = split_universe(results)
                self._metrics = metrics
                self._passed, self._controls = passed, controls
                skipped = getattr(self.source, "skipped", {})   # couldn't load: never screened
                return (f"{len(passed)} passed, {len(controls)} controls of {len(companies)}"
                        + (f", {len(skipped)} skipped" if skipped else ""),
                        {"passed": passed, "controls": controls, "results": results, "metrics": metrics,
                         "skipped": skipped})
            self._step(rid, "screen", screen)

            # 2 data --------------------------------------------------------
            def data():
                self._sections, self._anon, info = {}, {}, {}
                for t in self._passed + self._controls:
                    c = companies[t]
                    if c.filing_date >= as_of:
                        raise RuntimeError(f"{t}: filing dated {c.filing_date} is not before as_of")
                    sp = split_10k(c.filing_text)
                    self._sections[t] = sp["sections"]
                    self._anon[t] = anonymize_sections(sp["sections"], c.names)
                    info[t] = {"filing_date": c.filing_date.isoformat(), "missing_sections": sp["missing"],
                               "chars": {k: len(v) for k, v in sp["sections"].items()}}
                return f"{len(info)} filings split", info
            self._step(rid, "data", data)

            # 3 research ----------------------------------------------------
            self._step(rid, "research", lambda: (
                "filings only (backtest mode: web search disabled to avoid look-ahead)",
                {"mode": mode, "web_search": False}))

            # 4 score -------------------------------------------------------
            def score():
                named_runs, anon_runs, named_tot, anon_tot, cites, leaks, consensus = {}, {}, {}, {}, [], {}, {}
                usage = {"input_tokens": 0, "output_tokens": 0, "failed_runs": 0}
                for t in self._passed + self._controls:
                    m = self._metrics[t]
                    for variant, secs, cid in [("named", self._sections[t], t),
                                               ("anonymized", self._anon[t], f"ANON-{t}")]:
                        if variant == "anonymized" and not self.run_anonymized:
                            continue
                        for k in range(self.repeats):
                            res = score_company(self.llm, cid, secs, m, cfg.llm.max_retries)
                            self.store.save_scorecard(rid, t, variant, k, companies[t].filing_date.isoformat(), res)
                            usage["input_tokens"] += res.usage.get("input_tokens", 0)
                            usage["output_tokens"] += res.usage.get("output_tokens", 0)
                            if not res.ok:
                                usage["failed_runs"] += 1
                                continue
                            cites.append(res.citations)
                            dims = dimension_scores(res.card)
                            (named_runs if variant == "named" else anon_runs).setdefault(t, []).append(dims)
                            (named_tot if variant == "named" else anon_tot).setdefault(t, []).append(res.total)
                    if self.run_anonymized:
                        excerpt = " ".join(self._anon[t].values())
                        leaks[t] = leak_check(self.llm, f"ANON-{t}", excerpt, companies[t].names)["leaked"]
                    dims = _median_dims(named_runs.get(t, []))
                    tot = total_score({"scores": {d: {"score": v} for d, v in dims.items()}}) \
                        if named_runs.get(t) else None
                    consensus[t] = {"dims": dims, "total": tot}
                self._consensus = consensus
                self._check_inputs = dict(named_runs=named_runs, named_tot=named_tot,
                                          anon_tot=anon_tot, cites=cites, leaks=leaks)
                return (f"{len(consensus)} companies scored x{self.repeats}",
                        {"consensus": consensus, "leak_check": leaks, "usage": usage})
            self._step(rid, "score", score)

            # 5 value -------------------------------------------------------
            prices = self.source.prices

            def val_fn(t: str, d: date):
                c = companies[t]
                px = prices.close_on_or_before(t, d)
                oe = self._metrics[t].get("owner_earnings_last")
                mcap = px * c.shares_outstanding if px and c.shares_outstanding else None
                return valuation(oe, mcap, self.source.treasury_yield(d), cfg.decision.safety_margin)

            self._step(rid, "value", lambda: (
                f"valuation at {as_of}", {t: val_fn(t, as_of) for t in self._passed}))

            # 6-8 decide / risk / execute (one backtest loop) -------------
            snaps = [ScoreSnapshot(t, companies[t].filing_date, self._consensus[t]["total"],
                                   self._consensus[t]["dims"]) for t in self._passed]
            sectors = {t: companies[t].sector for t in self._passed}
            bt = run_backtest(cfg, prices, self._passed, sectors, snaps, val_fn,
                              decision_dates, end, model_cutoff)
            first = bt["log"][0] if bt["log"] else {}
            self._step(rid, "decide", lambda: (
                f"{len(bt['log'])} decision dates",
                {"first_date": first.get("decisions"),
                 "actions": [{"date": e["date"], "non_hold": [x for x in e["decisions"]
                                                             if x["action"] in ("BUY", "SELL")]}
                             for e in bt["log"]]}))
            self._step(rid, "risk", lambda: (
                f"{sum(len(e['rejected']) for e in bt['log'])} orders rejected",
                {"rejected": [{"date": e["date"], **r} for e in bt["log"] for r in e["rejected"]],
                 "limits": cfg.risk.__dict__}))
            for e in bt["log"]:
                self.store.save_decisions(rid, e["date"], e["decisions"])
            self.store.save_trades(rid, bt["trades"])
            self._step(rid, "execute", lambda: (
                f"{len(bt['trades'])} fills", {"trades": bt["trades"],
                                               "equity_curve": bt["equity_curve"]}))

            # 9 check -------------------------------------------------------
            ci = self._check_inputs

            def check():
                out = {
                    "consistency": checks.consistency(ci["named_runs"]),
                    "citations": checks.citation_rate(ci["cites"]),
                    "anonymization": checks.anonymization_gap(ci["named_tot"], ci["anon_tot"], ci["leaks"])
                    if self.run_anonymized else {"pass": None, "note": "disabled"},
                    "control_group": checks.control_separation(
                        {t: self._consensus[t]["total"] for t in self._passed},
                        {t: self._consensus[t]["total"] for t in self._controls}),
                    "backtest": bt["metrics"],
                }
                failed = [k for k, v in out.items() if isinstance(v, dict) and v.get("pass") is False]
                return (f"failed checks: {failed or 'none'}", out | {"failed": failed})
            self._step(rid, "check", check)
            self.store.finish_run(rid, "done")
        except Exception:
            self.store.finish_run(rid, "failed")
            raise
        return rid


def run_demo(store: Store, cfg: Config | None = None) -> str:
    from . import demo
    from .llm.client import ScriptedLLM
    resp, leak = demo.scripted_responses()
    pipe = Pipeline(cfg or Config(), demo.DemoSource(), ScriptedLLM(resp, leak), store, repeats=3)
    return pipe.run_backtest(demo.AS_OF, demo.decision_dates(), demo.END, demo.MODEL_CUTOFF, mode="demo")
