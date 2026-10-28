"""Offline tests. Run with `python -m unittest` (or `pytest`) from backend/."""
from __future__ import annotations

import json
import unittest
from datetime import date
from unittest import mock

import pandas as pd

from buffett_agent import demo
from buffett_agent.config import Config, CostConfig, DecisionConfig, RiskConfig, ScreenConfig, with_screen
from buffett_agent.data.prices import PriceStore
from buffett_agent.data.sections import split_10k
from buffett_agent.data.universe import sp500
from buffett_agent.data.xbrl import fundamentals_as_of, screening_metrics
from buffett_agent.llm.client import ScriptedLLM
from buffett_agent.pipeline import run_demo
from buffett_agent.research.anonymize import anonymize
from buffett_agent.research.citations import verify_quote, verify_scorecard
from buffett_agent.research.scoring import score_company
from buffett_agent.screening import screen_company, screen_pool
from buffett_agent.sources import EdgarSource
from buffett_agent.store import Store
from buffett_agent.trading.backtest import (LookAheadError, ScoreSnapshot, latest_snapshot,
                                            run_backtest)
from buffett_agent.trading.broker import PaperBroker
from buffett_agent.trading.decision import Decision, decide
from buffett_agent.trading.risk import apply_risk

DIMS5 = {"understandable": 5, "moat": 5, "management": 4, "financial_quality": 5, "risk": 4}
FAIR = {"fair_price": True, "overvalued": False, "owner_earnings_yield": 0.08,
        "hurdle": 0.062, "treasury_yield": 0.042}


class PointInTime(unittest.TestCase):
    def test_restatement_filed_after_as_of_is_ignored(self):
        facts = demo._facts("NFBR")
        before = fundamentals_as_of(facts, date(2026, 7, 1)).annual
        after = fundamentals_as_of(facts, date(2026, 10, 1)).annual
        ni_2025 = lambda a: a.loc[date(2025, 12, 31), "net_income"]  # noqa: E731
        self.assertGreater(ni_2025(before), 0)
        self.assertLess(ni_2025(after), 0)   # the restatement only shows up once filed

    def test_year_not_yet_filed_is_absent(self):
        a = fundamentals_as_of(demo._facts("NFBR"), date(2026, 2, 20)).annual
        self.assertNotIn(date(2025, 12, 31), a.index)  # FY2025 10-K filed that day, not before

    def test_snapshot_only_usable_after_filing_date(self):
        snaps = [ScoreSnapshot("X", date(2026, 3, 1), 4.5, DIMS5)]
        self.assertIsNone(latest_snapshot(snaps, "X", date(2026, 3, 1)))
        self.assertIsNotNone(latest_snapshot(snaps, "X", date(2026, 3, 2)))

    def test_backtest_refuses_to_start_before_model_cutoff(self):
        with self.assertRaises(LookAheadError):
            run_backtest(Config(), demo.demo_prices(), ["NFBR"], {}, [], lambda t, d: None,
                         [date(2026, 6, 1)], date(2026, 7, 1), model_cutoff=date(2026, 6, 30))

    def test_fill_is_next_open_not_same_day(self):
        days = [date(2026, 7, 6), date(2026, 7, 7)]
        ps = PriceStore({"X": pd.DataFrame({"open": [10.0, 11.0], "close": [10.5, 11.5]}, index=days)})
        self.assertEqual(ps.next_open("X", date(2026, 7, 6)), (date(2026, 7, 7), 11.0))
        self.assertIsNone(ps.next_open("X", date(2026, 7, 7)))


class Metrics(unittest.TestCase):
    def test_negative_equity_gives_undefined_roe(self):
        facts = demo._facts("NFBR")
        for r in facts["facts"]["us-gaap"]["StockholdersEquity"]["units"]["USD"]:
            r["val"] = -1.0
        m = screening_metrics(fundamentals_as_of(facts, date(2026, 7, 1)))
        self.assertTrue(all(v is None for v in m["roe_by_year"].values()))

    def test_tags_used_are_reported(self):
        f = fundamentals_as_of(demo._facts("NFBR"), date(2026, 7, 1))
        self.assertEqual(f.tags_used["net_income"], "NetIncomeLoss")


    def test_tag_switch_fills_later_years_from_next_candidate(self):
        def row(y, v):
            return {"form": "10-K", "fp": "FY", "end": f"{y}-12-31", "filed": f"{y + 1}-02-20", "val": v}
        facts = {"facts": {"us-gaap": {
            "LongTermDebtNoncurrent": {"units": {"USD": [row(2022, 30), row(2023, 31)]}},
            "LongTermDebtAndCapitalLeaseObligations": {"units": {"USD": [row(2023, 99), row(2024, 32)]}},
        }}}
        f = fundamentals_as_of(facts, date(2026, 1, 1))
        self.assertEqual(list(f.annual["lt_debt"]), [30, 31, 32])   # 2023 keeps the preferred tag
        self.assertEqual(f.tags_used["lt_debt"],
                         "LongTermDebtNoncurrent + LongTermDebtAndCapitalLeaseObligations")


    def test_missing_latest_debt_counts_as_zero_and_is_flagged(self):
        def row(y, v):
            return {"form": "10-K", "fp": "FY", "end": f"{y}-12-31", "filed": f"{y + 1}-02-20", "val": v}
        def flow(y, v):
            return dict(row(y, v), start=f"{y}-01-01")
        facts = {"facts": {"us-gaap": {
            "NetIncomeLoss": {"units": {"USD": [flow(2022, 10), flow(2023, 12)]}},
            "LongTermDebtNoncurrent": {"units": {"USD": [row(2022, 5)]}},   # repaid in 2023
        }}}
        m = screening_metrics(fundamentals_as_of(facts, date(2026, 1, 1)))
        self.assertEqual(m["debt_to_income"], 0.0)
        self.assertTrue(m["debt_assumed_zero"])

    def _no_debt_tag_facts(self, **extra):
        def flow(y, v):
            return {"form": "10-K", "start": f"{y}-01-01", "end": f"{y}-12-31", "filed": f"{y + 1}-02-20", "val": v}
        g = {"NetIncomeLoss": {"units": {"USD": [flow(2022, 10), flow(2023, 12)]}}}
        for tag, rows in extra.items():
            g[tag] = {"units": {"USD": rows}}
        return {"facts": {"us-gaap": g}}

    def test_untagged_debt_balance_is_used_not_zero(self):
        bal = {"form": "10-K", "end": "2023-12-31", "filed": "2024-02-20"}
        facts = self._no_debt_tag_facts(NotesPayable=[dict(bal, val=60)],
                                        AvailableForSaleSecuritiesDebtSecurities=[dict(bal, val=999)])
        m = screening_metrics(fundamentals_as_of(facts, date(2026, 1, 1)))
        self.assertEqual(m["debt_to_income"], 5.0)            # 60 / 12; the securities are assets
        self.assertEqual(m["debt_source"], "NotesPayable")

    def test_real_interest_without_debt_balance_is_unknown(self):
        facts = self._no_debt_tag_facts(InterestExpense=[
            {"form": "10-K", "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-20", "val": 3}])
        m = screening_metrics(fundamentals_as_of(facts, date(2026, 1, 1)))
        self.assertTrue(m["debt_unknown"])                    # 3 / 12 = 25% of net income
        self.assertFalse(screen_company(m, ScreenConfig())["checks"]["debt"]["pass"])

    def test_financials_use_roa_and_capital_instead_of_debt(self):
        roa = pd.Series([0.012] * 10)
        m = {"_roe": pd.Series([0.16] * 10), "_roa": roa, "equity_to_assets": 0.09,
             "profitable_years": 10, "debt_to_income": 30.0}   # a bank's deposits-funded balance sheet
        self.assertFalse(screen_company(m, ScreenConfig())["passed"])
        r = screen_company(m, ScreenConfig(), "Financials")
        self.assertTrue(r["passed"])
        self.assertTrue(r["checks"]["debt"]["na"])
        self.assertFalse(screen_company(dict(m, equity_to_assets=0.05), ScreenConfig(), "Financials")["passed"])


    def test_gross_profit_is_derived_for_years_it_was_not_tagged(self):
        def flow(y, v):
            return {"form": "10-K", "start": f"{y}-01-01", "end": f"{y}-12-31", "filed": f"{y + 1}-02-20", "val": v}
        facts = {"facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [flow(2018, 100), flow(2019, 110), flow(2020, 120)]}},
            "GrossProfit": {"units": {"USD": [flow(2018, 13)]}},        # stopped tagging after 2018
            "CostOfGoodsAndServicesSold": {"units": {"USD": [flow(2019, 96), flow(2020, 105)]}},
        }}}
        gp = fundamentals_as_of(facts, date(2026, 1, 1)).annual["gross_profit"]
        self.assertEqual(list(gp), [13, 14, 15])


class Sections(unittest.TestCase):
    def test_skips_table_of_contents(self):
        out = split_10k(demo.filing_text("NFBR"))
        self.assertEqual(out["missing"], [])
        self.assertIn("flagship brands", out["sections"]["item_1"])
        self.assertNotIn("Risk Factors 9", out["sections"]["item_1"])


class Citations(unittest.TestCase):
    def test_whitespace_and_curly_quotes_are_tolerated(self):
        self.assertTrue(verify_quote("we  don’t   sell", "We don't sell data."))

    def test_paraphrase_fails(self):
        self.assertFalse(verify_quote("we never sell data", "We don't sell data."))

    def test_unverified_score_is_nulled(self):
        card = {"scores": {"moat": {"score": 5, "evidence": [{"section": "item_1", "quote": "made up"}]}}}
        rep = verify_scorecard(card, {"item_1": "real text"})
        self.assertIsNone(card["scores"]["moat"]["score"])
        self.assertEqual(rep["verified"], 0)


class Scoring(unittest.TestCase):
    def test_malformed_json_is_retried_then_fails_closed(self):
        llm = ScriptedLLM({"X": ["{bad", "{bad", "{bad"]})
        res = score_company(llm, "X", {"item_1": "t"}, {}, max_retries=2)
        self.assertFalse(res.ok)
        self.assertEqual(res.attempts, 3)

    def test_recovers_after_one_bad_answer(self):
        resp, _ = demo.scripted_responses()
        good = resp["RRSV"][0]
        llm = ScriptedLLM({"RRSV": ["{bad", good]})
        secs = split_10k(demo.filing_text("RRSV"))["sections"]
        res = score_company(llm, "RRSV", secs, {}, max_retries=2)
        self.assertTrue(res.ok)
        self.assertEqual(res.attempts, 2)
        self.assertAlmostEqual(res.total, 4.2)

    def test_anonymize_removes_names(self):
        t = anonymize("Northfield Brands (NFBR) sells soup. Northfield grows.", ["Northfield Brands", "NFBR"])
        self.assertNotIn("Northfield Brands", t)
        self.assertNotIn("NFBR", t)


class Decisions(unittest.TestCase):
    cfg = DecisionConfig()

    def test_buy_needs_quality_and_price(self):
        self.assertEqual(decide("X", 4.6, DIMS5, FAIR, False, None, None, self.cfg).action, "BUY")
        expensive = FAIR | {"fair_price": False}
        self.assertEqual(decide("X", 4.6, DIMS5, expensive, False, None, None, self.cfg).action, "SKIP")

    def test_dimension_floor_blocks_buy(self):
        dims = DIMS5 | {"risk": 1}
        self.assertEqual(decide("X", 4.0, dims, FAIR, False, None, None, self.cfg).action, "SKIP")

    def test_sell_reasons_are_distinguished(self):
        d = decide("X", 3.0, DIMS5, FAIR, True, 100, 100, self.cfg)
        self.assertEqual(d.rule, "sell_fundamental_total")
        d = decide("X", 4.6, DIMS5, FAIR | {"overvalued": True}, True, 100, 100, self.cfg)
        self.assertEqual(d.rule, "sell_valuation")
        d = decide("X", 4.6, DIMS5, FAIR, True, 100, 79, self.cfg)
        self.assertEqual(d.rule, "sell_stop_loss")

    def test_no_scorecard_never_buys(self):
        self.assertEqual(decide("X", None, None, FAIR, False, None, None, self.cfg).action, "SKIP")

    def test_cooldown_blocks_rebuy(self):
        d = decide("X", 4.6, DIMS5, FAIR, False, None, None, self.cfg, in_cooldown=True)
        self.assertEqual(d.rule, "skip_cooldown_after_stop")


class Risk(unittest.TestCase):
    def test_limits(self):
        cfg = RiskConfig(max_position_weight=0.1, max_sector_weight=0.15, max_new_positions_per_step=2)
        buys = [Decision(t, "BUY", "r") for t in ["A", "B", "C"]]
        orders, rej = apply_risk(buys, 100_000, 100_000, {}, {"A": "s1", "B": "s1", "C": "s2"}, cfg)
        notional = {o["ticker"]: o["notional"] for o in orders}
        self.assertEqual(notional["A"], 10_000)
        self.assertEqual(notional["B"], 5_000)          # sector cap leaves only 5k
        self.assertEqual(rej, [{"ticker": "C", "reason": "max_new_positions_per_step"}])

    def test_kill_switch(self):
        orders, rej = apply_risk([Decision("A", "BUY", "r")], 1e5, 1e5, {}, {},
                                 RiskConfig(trading_enabled=False))
        self.assertEqual(orders, [])
        self.assertEqual(rej[0]["reason"], "kill_switch_off")


class Broker(unittest.TestCase):
    def test_round_trip_pays_costs(self):
        b = PaperBroker(10_000, CostConfig(commission_bps=10, spread_bps=10))
        b.buy("X", 10_000, date(2026, 7, 7), 100.0)
        b.sell("X", date(2026, 7, 8), 100.0)
        self.assertAlmostEqual(b.cash, 10_000 * 0.998 * 0.998, places=6)


class Universe(unittest.TestCase):
    def test_sp500_snapshot_loads_with_sectors_and_ciks(self):
        members = sp500()
        self.assertGreater(len(members), 495)
        by_ticker = {m.ticker: m for m in members}
        self.assertEqual(by_ticker["BRK-B"].cik, 1067983)              # SEC-style ticker
        self.assertEqual(by_ticker["AAPL"].sector, "Information Technology")
        self.assertEqual(len({m.sector for m in members}), 11)          # GICS sectors

    def test_members_added_after_as_of_are_excluded(self):
        as_of = date(2020, 1, 1)
        members = sp500(as_of)
        self.assertLess(len(members), len(sp500()))
        self.assertTrue(all(m.date_added <= as_of for m in members))

    def test_no_tickers_defaults_to_sp500_and_bad_companies_are_skipped(self):
        class FakeClient:
            def __init__(self, *a, **kw): pass
            def ticker_map(self): return {}
            def company_facts(self, cik): raise RuntimeError("SEC refused")

        with mock.patch("buffett_agent.data.edgar.EdgarClient", FakeClient):
            src = EdgarSource.sp500(date(2026, 7, 1), PriceStore({}), 0.042)
            self.assertEqual(len(src.tickers), len(sp500(date(2026, 7, 1))))
            self.assertEqual(src.sectors["KO"], "Consumer Staples")
            self.assertEqual(src.companies(date(2026, 7, 1)), [])     # run continues
            self.assertIn("SEC refused", src.skipped["KO"])


class ScreenOnly(unittest.TestCase):
    def test_screen_pool_matches_pipeline_and_skips_failures(self):
        def load(t):
            if t == "BAD":
                raise RuntimeError("SEC refused")
            return demo._facts(t)

        tickers = list(demo.PROFILES) + ["BAD"]
        rep = screen_pool(load, tickers, demo.AS_OF, Config().screen)
        self.assertEqual(rep["passed"], ["CPLF", "HLNF", "NFBR", "RRSV"])   # same as demo step 1
        self.assertEqual(rep["screened"], 6)
        self.assertIn("SEC refused", rep["skipped"]["BAD"])
        self.assertEqual(rep["thresholds"]["roe_min"], 0.15)               # recorded
        json.dumps(rep)


class EndToEnd(unittest.TestCase):
    def test_demo_run_records_all_steps_and_findings(self):
        store = Store(":memory:")
        rid = run_demo(store)
        steps = {s["step"]: s for s in store.get_steps(rid)}
        self.assertEqual(list(steps), ["screen", "data", "research", "score", "value",
                                       "decide", "risk", "execute", "check"])
        self.assertTrue(all(s["status"] == "ok" for s in steps.values()))
        self.assertEqual(steps["screen"]["output"]["controls"], ["GLMR", "VNTR"])
        score = steps["score"]["output"]
        self.assertIsNone(score["consensus"]["HLNF"]["total"])        # fabricated quote
        self.assertTrue(score["leak_check"]["NFBR"])                  # anonymization leaked
        trades = steps["execute"]["output"]["trades"]
        self.assertNotIn("CPLF", {t["ticker"] for t in trades})      # too expensive
        self.assertIn("sell_stop_loss", {t["rule"] for t in trades})
        rebuys = [t for t in trades if t["ticker"] == "RRSV" and t["side"] == "BUY"]
        self.assertEqual(len(rebuys), 1)                              # cooldown held
        json.dumps(steps["check"]["output"])                          # serializable for the UI

    def test_screen_overrides_change_screen_and_are_recorded(self):
        store = Store(":memory:")
        rid = run_demo(store, with_screen(Config(), {"debt_to_income_max": 2.0}))
        screen = {s["step"]: s for s in store.get_steps(rid)}["screen"]["output"]
        self.assertNotIn("RRSV", screen["passed"])                    # debt 2.43x > 2.0x
        self.assertIn("NFBR", screen["passed"])                       # debt 1.67x
        self.assertEqual(store.list_runs()[0]["screen"]["debt_to_income_max"], 2.0)

    def test_screen_overrides_are_validated(self):
        with self.assertRaises(ValueError):
            with_screen(Config(), {"profitable_years_required": 12})  # > lookback of 10
        with self.assertRaises(ValueError):
            with_screen(Config(), {"no_such_rule": 1})


class ScreenReports(unittest.TestCase):
    """The UI reads pool screens written by `python -m buffett_agent screen`."""

    def setUp(self):
        import os, tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["BUFFETT_DB"] = str(self.tmp / "t.db")   # importing api opens a store
        from buffett_agent import api
        self.api = api
        self._dir, api.screens_dir = api.screens_dir, self.tmp

    def tearDown(self):
        self.api.screens_dir = self._dir

    def test_lists_and_reads_reports_but_no_other_files(self):
        from fastapi import HTTPException
        rep = {"as_of": "2026-10-27", "pool": 2, "screened": 2, "passed": ["KO"],
               "results": {"KO": {"passed": True, "checks": {"debt": {"value": float("inf")}}}}}
        (self.tmp / "screen_2026-10-27.json").write_text(json.dumps(rep))
        (self.tmp / "secret.json").write_text("{}")
        self.assertEqual(self.api.screen_reports(), [{"name": "screen_2026-10-27.json", "as_of": "2026-10-27",
                                                      "pool": 2, "screened": 2, "passed": 1}])
        got = self.api.screen_report("screen_2026-10-27.json")
        self.assertIsNone(got["results"]["KO"]["checks"]["debt"]["value"])   # Infinity -> null
        for bad in ("secret.json", "../screen_x.json", "screen_missing.json"):
            with self.assertRaises(HTTPException):
                self.api.screen_report(bad)

    def test_results_carry_rule_group(self):
        roa = pd.Series([0.012] * 10)
        m = {"_roe": pd.Series([0.16] * 10), "_roa": roa, "equity_to_assets": 0.09, "profitable_years": 10}
        self.assertEqual(screen_company(m, ScreenConfig(), "Financials")["group"], "financials")
        self.assertEqual(screen_company(m, ScreenConfig(), "Utilities")["group"], "provisional")
        self.assertEqual(screen_company(m, ScreenConfig(), None)["group"], "general")


if __name__ == "__main__":
    unittest.main()
