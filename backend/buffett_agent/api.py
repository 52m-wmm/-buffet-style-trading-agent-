"""HTTP API for the canvas UI. Thin by design: all logic lives in pipeline.py and
can be run without the API (see __main__.py).

Run:  uvicorn buffett_agent.api:app --reload --port 8000
"""
from __future__ import annotations

import math
import os
from dataclasses import asdict
from datetime import date, timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .config import load_config, with_screen
from .pipeline import EDGES, STEPS, Pipeline, run_demo
from .store import Store

app = FastAPI(title="Buffett-style trading agent")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                   allow_methods=["*"], allow_headers=["*"])
store = Store(os.environ.get("BUFFETT_DB", "buffett_agent.db"))
cfg = load_config(os.environ.get("BUFFETT_CONFIG", "config.yaml"))


class ScreenOverrides(BaseModel):
    """Screen thresholds a run may override; anything omitted keeps the server config."""
    model_config = ConfigDict(extra="forbid")
    roe_min: float | None = Field(None, ge=0, le=1)
    roe_years_required: int | None = Field(None, ge=0, le=30)
    debt_to_income_max: float | None = Field(None, ge=0)
    profitable_years_required: int | None = Field(None, ge=0, le=30)
    fcf_years: int | None = Field(None, ge=1, le=30)
    gross_margin_std_max: float | None = Field(None, gt=0, le=1)
    lookback_years: int | None = Field(None, ge=1, le=30)


class RunRequest(BaseModel):
    mode: Literal["demo", "backtest"] = "demo"
    screen: ScreenOverrides | None = None
    tickers: list[str] = []
    sectors: dict[str, str] = {}
    as_of: date | None = None
    end: date | None = None
    model_cutoff: date | None = None
    prices_dir: str = "prices"
    treasury: float = 0.042
    repeats: int = 3


@app.get("/api/pipeline")
def pipeline_graph():
    return {"steps": STEPS, "edges": [{"source": a, "target": b} for a, b in EDGES]}


@app.get("/api/config/screen")
def screen_defaults():
    return asdict(cfg.screen)


@app.get("/api/runs")
def runs():
    return store.list_runs()


@app.post("/api/runs")
def create_run(req: RunRequest):
    try:
        run_cfg = with_screen(cfg, req.screen.model_dump(exclude_none=True) if req.screen else {})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if req.mode == "demo":
        return {"id": run_demo(store, run_cfg)}
    # Real data. Runs synchronously; move to a job queue before scaling up.
    from .data.prices import PriceStore
    from .llm.client import DeepSeekClient
    from .sources import EdgarSource
    if not (req.as_of and req.end and req.model_cutoff):
        raise HTTPException(400, "backtest needs as_of, end and model_cutoff")
    prices = PriceStore.from_dir(req.prices_dir)
    if req.tickers:
        src = EdgarSource(req.tickers, req.sectors, prices, req.treasury)
    else:  # default pool: S&P 500 members as of as_of
        src = EdgarSource.sp500(req.as_of, prices, req.treasury)
        src.sectors.update(req.sectors)
    pipe = Pipeline(run_cfg, src, DeepSeekClient(run_cfg.llm), store, repeats=req.repeats)
    dates, d = [], req.as_of + timedelta(days=(7 - req.as_of.weekday()) % 7)
    while d <= req.end:
        dates.append(d)
        d += timedelta(days=7)
    try:
        return {"id": pipe.run_backtest(req.as_of, dates, req.end, req.model_cutoff)}
    except Exception as e:  # the failed step is already stored with its traceback
        raise HTTPException(500, f"{type(e).__name__}: {e}") from e


def _finite(o):
    # Strict JSON has no Infinity/NaN (e.g. debt_to_income when net income <= 0); send null.
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_finite(v) for v in o]
    return o


@app.get("/api/runs/{run_id}/steps")
def steps(run_id: str):
    out = store.get_steps(run_id)
    if not out:
        raise HTTPException(404, "run not found")
    return _finite(out)
