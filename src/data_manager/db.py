"""SQLite storage for the data-manager."""

import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("DATA_MANAGER_DB", "~/.prime/agent/data_manager.db")).expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS universe (
    ticker        TEXT PRIMARY KEY,
    name          TEXT,
    source        TEXT,
    added_at      TEXT,
    figi          TEXT,
    cik           TEXT,
    sic           TEXT,
    sic_description TEXT,
    lei           TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    ticker    TEXT,
    date      TEXT,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    INTEGER,
    adjustment REAL,  -- factor = yf Adj Close / Close (splits + dividends); apply at QUERY time
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker ON prices(ticker);

CREATE TABLE IF NOT EXISTS classifications (
    ticker   TEXT PRIMARY KEY,
    sector   TEXT,
    industry TEXT,
    as_of    TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT,
    pulled_at  TEXT,
    as_of      TEXT,
    row_count  INTEGER
);

CREATE TABLE IF NOT EXISTS quarterly_statements (
    ticker TEXT, period TEXT,
    net_income REAL, revenue REAL, gross_profit REAL, operating_cash_flow REAL,
    total_assets REAL, total_liabilities REAL, current_assets REAL, current_liabilities REAL,
    shares_out REAL, roa REAL, cfo REAL,
    PRIMARY KEY (ticker, period)
);

CREATE TABLE IF NOT EXISTS ratios (
    ticker   TEXT,
    as_of    TEXT,
    trailing_pe REAL, forward_pe REAL, price_to_book REAL, price_to_sales REAL,
    roe REAL, roa REAL, net_margin REAL, gross_margin REAL, operating_margin REAL,
    debt_to_equity REAL, current_ratio REAL, dividend_yield REAL, market_cap REAL,
    enterprise_value REAL, ev_to_ebitda REAL, beta REAL, shares_outstanding REAL,
    PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS fundamentals (
    ticker          TEXT,
    fiscal_year     INTEGER,
    roa             REAL,
    cfo             REAL,
    d_roa           REAL,
    accruals        REAL,
    d_leverage      REAL,
    d_liquidity     REAL,
    equity_issuance REAL,
    d_gross_margin  REAL,
    d_asset_turnover REAL,
    f_score         INTEGER,
    PRIMARY KEY (ticker, fiscal_year)
);
"""


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection to the SQLite DB, creating schema if needed."""
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")      # concurrent readers/writers across processes
    conn.execute("PRAGMA busy_timeout=20000")    # wait for brief locks instead of crashing
    conn.executescript(SCHEMA)

    try:
        conn.execute("ALTER TABLE prices ADD COLUMN adjustment REAL")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE universe ADD COLUMN figi TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE universe ADD COLUMN cik TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE universe ADD COLUMN sic TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE universe ADD COLUMN sic_description TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE universe ADD COLUMN lei TEXT")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    return conn
