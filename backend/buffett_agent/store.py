"""Persistence. Uses the standard-library sqlite3 driver by default; the schema is
plain SQL so it also runs on PostgreSQL (swap the connection for psycopg)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = "buffett_agent.db"):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def new_run(self, mode: str, config: dict) -> str:
        rid = uuid.uuid4().hex[:12]
        self.conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                          (rid, mode, _now(), None, "running", json.dumps(config)))
        self.conn.commit()
        return rid

    def finish_run(self, rid: str, status: str) -> None:
        self.conn.execute("UPDATE runs SET finished_at=?, status=? WHERE id=?", (_now(), status, rid))
        self.conn.commit()

    def save_step(self, rid: str, step: str, status: str, started: str, summary: str, output) -> None:
        self.conn.execute("INSERT OR REPLACE INTO step_runs VALUES (?,?,?,?,?,?,?)",
                          (rid, step, status, started, _now(), summary,
                           json.dumps(output, default=str, ensure_ascii=False)))
        self.conn.commit()

    def save_scorecard(self, rid, company_id, variant, attempt_no, source_filed, res) -> None:
        self.conn.execute(
            "INSERT INTO scorecards VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, company_id, variant, attempt_no, source_filed, int(res.ok), res.total,
             json.dumps(res.card, ensure_ascii=False) if res.card else None,
             json.dumps(res.citations, ensure_ascii=False) if res.citations else None,
             json.dumps(res.usage), res.error))
        self.conn.commit()

    def save_decisions(self, rid, decided_on, decisions) -> None:
        self.conn.executemany("INSERT INTO decisions VALUES (?,?,?,?,?,?)",
                              [(rid, decided_on, d["ticker"], d["action"], d["rule"], d["detail"])
                               for d in decisions])
        self.conn.commit()

    def save_trades(self, rid, trades) -> None:
        self.conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?)",
                              [(rid, t["date"], t["ticker"], t["side"], t["price"], t["shares"],
                                t["notional"], t["fee"], t.get("rule")) for t in trades])
        self.conn.commit()

    # -- reads for the API ---------------------------------------------------
    def list_runs(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute("SELECT id, mode, started_at, finished_at, status, config_json FROM runs "
                                 "ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["screen"] = json.loads(d.pop("config_json")).get("screen")  # thresholds this run used
            out.append(d)
        return out

    def get_steps(self, rid: str) -> list[dict]:
        rows = self.conn.execute("SELECT step, status, started_at, finished_at, summary, output_json "
                                 "FROM step_runs WHERE run_id=? ORDER BY rowid", (rid,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["output"] = json.loads(d.pop("output_json") or "null")
            out.append(d)
        return out
