-- Runs on SQLite (default) and PostgreSQL. JSON columns are TEXT for portability;
-- on Postgres you can switch them to JSONB.

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    mode        TEXT NOT NULL,            -- demo | backtest | forward
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,            -- running | done | failed
    config_json TEXT NOT NULL             -- thresholds frozen for this run
);

CREATE TABLE IF NOT EXISTS step_runs (
    run_id      TEXT NOT NULL REFERENCES runs(id),
    step        TEXT NOT NULL,            -- screen | data | research | score | value | decide | risk | execute | check
    status      TEXT NOT NULL,            -- ok | failed | skipped
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    summary     TEXT,
    output_json TEXT,
    PRIMARY KEY (run_id, step)
);

CREATE TABLE IF NOT EXISTS scorecards (
    run_id        TEXT NOT NULL REFERENCES runs(id),
    company_id    TEXT NOT NULL,
    variant       TEXT NOT NULL,          -- named | anonymized
    attempt_no    INTEGER NOT NULL,       -- repeat index for consistency tests
    source_filed  TEXT,                   -- filing date of the document scored
    ok            INTEGER NOT NULL,
    total         REAL,
    card_json     TEXT,
    citations_json TEXT,
    usage_json    TEXT,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    run_id      TEXT NOT NULL REFERENCES runs(id),
    decided_on  TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    action      TEXT NOT NULL,
    rule        TEXT NOT NULL,
    detail      TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    run_id      TEXT NOT NULL REFERENCES runs(id),
    filled_on   TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL,
    price       REAL NOT NULL,
    shares      REAL NOT NULL,
    notional    REAL NOT NULL,
    fee         REAL NOT NULL,
    rule        TEXT
);
