"""SQLite storage for the data-manager."""

import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("DATA_MANAGER_DB", "~/.prime/agent/data_manager.db")).expanduser()

SF1_INDICATORS = [
    "accoci",
    "assets",
    "assetsavg",
    "assetsc",
    "assetsnc",
    "assetturnover",
    "bvps",
    "capex",
    "cashneq",
    "cashnequsd",
    "cor",
    "consolinc",
    "currentratio",
    "de",
    "debt",
    "debtc",
    "debtnc",
    "debtusd",
    "deferredrev",
    "depamor",
    "deposits",
    "divyield",
    "dps",
    "ebit",
    "ebitda",
    "ebitdamargin",
    "ebitdausd",
    "ebitusd",
    "ebt",
    "eps",
    "epsdil",
    "epsusd",
    "equity",
    "equityavg",
    "equityusd",
    "ev",
    "evebit",
    "evebitda",
    "fcf",
    "fcfps",
    "fxusd",
    "gp",
    "grossmargin",
    "intangibles",
    "intexp",
    "invcap",
    "invcapavg",
    "inventory",
    "investments",
    "investmentsc",
    "investmentsnc",
    "liabilities",
    "liabilitiesc",
    "liabilitiesnc",
    "marketcap",
    "ncf",
    "ncfbus",
    "ncfcommon",
    "ncfdebt",
    "ncfdiv",
    "ncff",
    "ncfi",
    "ncfinv",
    "ncfo",
    "ncfx",
    "netinc",
    "netinccmn",
    "netinccmnusd",
    "netincdis",
    "netincnci",
    "netmargin",
    "opex",
    "opinc",
    "payables",
    "payoutratio",
    "pb",
    "pe",
    "pe1",
    "ppnenet",
    "prefdivis",
    "price",
    "ps",
    "ps1",
    "receivables",
    "retearn",
    "revenue",
    "revenueusd",
    "rnd",
    "roa",
    "roe",
    "roic",
    "ros",
    "sbcomp",
    "sgna",
    "sharefactor",
    "sharesbas",
    "shareswa",
    "shareswadil",
    "sps",
    "tangibles",
    "taxassets",
    "taxexp",
    "taxliabilities",
    "tbvps",
    "workingcapital",
]



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
-- date-first index: cross-sectional / PIT "all tickers as of date D" scans
-- (backtests, universe construction, rolling snapshots) are range reads
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date, ticker, close, volume);

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


CREATE TABLE IF NOT EXISTS securities_master (
    permaticker  TEXT PRIMARY KEY,
    ticker       TEXT, name TEXT, exchange TEXT, isdelisted TEXT,
    category     TEXT, cusips TEXT, siccode TEXT, sicsector TEXT,
    sicindustry  TEXT, figi TEXT, famaindustry TEXT,
    sector       TEXT, industry TEXT,
    scalemarketcap TEXT, scalerevenue TEXT, relatedtickers TEXT,
    currency     TEXT, location TEXT,
    firstadded   TEXT, firstpricedate TEXT, lastpricedate TEXT,
    firstquarter TEXT, lastquarter TEXT,
    secfilings   TEXT, companysite TEXT, lastupdated TEXT
);
CREATE TABLE IF NOT EXISTS corporate_actions (
    ticker TEXT, date TEXT, action TEXT, name TEXT, value TEXT,
    contraticker TEXT, contraname TEXT,
    PRIMARY KEY (ticker, date, action, value)
);

CREATE TABLE IF NOT EXISTS descriptions (
    table_name   TEXT,
    indicator    TEXT,
    isfilter     TEXT,
    isprimarykey TEXT,
    title        TEXT,
    description  TEXT,
    unittype     TEXT,
    PRIMARY KEY (table_name, indicator)
);

CREATE TABLE IF NOT EXISTS sp500_membership (
    ticker TEXT, date TEXT, action TEXT, name TEXT,
    contraticker TEXT, contraname TEXT, note TEXT,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS sf1 (
    ticker TEXT, dimension TEXT, date TEXT, reportperiod TEXT,
    fiscalperiod TEXT, calendardate TEXT, lastupdated TEXT,
    accoci REAL,
    assets REAL,
    assetsavg REAL,
    assetsc REAL,
    assetsnc REAL,
    assetturnover REAL,
    bvps REAL,
    capex REAL,
    cashneq REAL,
    cashnequsd REAL,
    cor REAL,
    consolinc REAL,
    currentratio REAL,
    de REAL,
    debt REAL,
    debtc REAL,
    debtnc REAL,
    debtusd REAL,
    deferredrev REAL,
    depamor REAL,
    deposits REAL,
    divyield REAL,
    dps REAL,
    ebit REAL,
    ebitda REAL,
    ebitdamargin REAL,
    ebitdausd REAL,
    ebitusd REAL,
    ebt REAL,
    eps REAL,
    epsdil REAL,
    epsusd REAL,
    equity REAL,
    equityavg REAL,
    equityusd REAL,
    ev REAL,
    evebit REAL,
    evebitda REAL,
    fcf REAL,
    fcfps REAL,
    fxusd REAL,
    gp REAL,
    grossmargin REAL,
    intangibles REAL,
    intexp REAL,
    invcap REAL,
    invcapavg REAL,
    inventory REAL,
    investments REAL,
    investmentsc REAL,
    investmentsnc REAL,
    liabilities REAL,
    liabilitiesc REAL,
    liabilitiesnc REAL,
    marketcap REAL,
    ncf REAL,
    ncfbus REAL,
    ncfcommon REAL,
    ncfdebt REAL,
    ncfdiv REAL,
    ncff REAL,
    ncfi REAL,
    ncfinv REAL,
    ncfo REAL,
    ncfx REAL,
    netinc REAL,
    netinccmn REAL,
    netinccmnusd REAL,
    netincdis REAL,
    netincnci REAL,
    netmargin REAL,
    opex REAL,
    opinc REAL,
    payables REAL,
    payoutratio REAL,
    pb REAL,
    pe REAL,
    pe1 REAL,
    ppnenet REAL,
    prefdivis REAL,
    price REAL,
    ps REAL,
    ps1 REAL,
    receivables REAL,
    retearn REAL,
    revenue REAL,
    revenueusd REAL,
    rnd REAL,
    roa REAL,
    roe REAL,
    roic REAL,
    ros REAL,
    sbcomp REAL,
    sgna REAL,
    sharefactor REAL,
    sharesbas REAL,
    shareswa REAL,
    shareswadil REAL,
    sps REAL,
    tangibles REAL,
    taxassets REAL,
    taxexp REAL,
    taxliabilities REAL,
    tbvps REAL,
    workingcapital REAL,
    PRIMARY KEY (ticker, dimension, reportperiod)
);
CREATE INDEX IF NOT EXISTS idx_sf1_ticker_dim ON sf1(ticker, dimension, date);

CREATE TABLE IF NOT EXISTS metrics (
    ticker TEXT, as_of TEXT,
    price REAL, beta1y REAL, beta5y REAL,
    ma50d REAL, ma200d REAL, high52w REAL, low52w REAL,
    return1y REAL, return5y REAL, returnytd REAL,
    volume REAL, volumeavg1m REAL, volumeavg3m REAL,
    dividendyieldtrailing REAL, dividendyieldforward REAL,
    high5y REAL, low5y REAL,
    PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS french_factors (
    date   TEXT PRIMARY KEY,   -- trading day YYYY-MM-DD (source datekey is YYYYMMDD)
    mkt_rf REAL,   -- %/day  market excess return (value-weight CRSP universe minus RF)
    smb    REAL,   -- %/day  small-minus-big (size)
    hml    REAL,   -- %/day  high-minus-low book-to-market (value)
    rmw    REAL,   -- %/day  robust-minus-weak operating profitability
    cma    REAL,   -- %/day  conservative-minus-aggressive investment
    mom    REAL,   -- %/day  momentum (prior 2-12 month return)
    st_rev REAL,   -- %/day  short-term reversal (prior 1-1 month return)
    lt_rev REAL,   -- %/day  long-term reversal (prior 13-60 month return)
    rf     REAL    -- %/day  daily risk-free rate (compounds to 1-month T-bill)
);

CREATE TABLE IF NOT EXISTS universe_pit (
    as_of TEXT, ticker TEXT,
    category TEXT, exchange TEXT, isdelisted TEXT,
    sector TEXT, industry TEXT,
    price REAL, mcap REAL,
    dvol_avg REAL, dvol_days INTEGER,
    firstpricedate TEXT, lastpricedate TEXT,
    PRIMARY KEY (as_of, ticker)
);
CREATE INDEX IF NOT EXISTS idx_pit_asof ON universe_pit(as_of);

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
    # read/write tuning for the warehouse (research workload)
    conn.execute("PRAGMA synchronous=NORMAL")    # WAL-safe; checkpoints fsync, not every txn
    conn.execute("PRAGMA journal_size_limit=536870912")   # cap WAL growth (512MB) -> no checkpoint stalls
    conn.execute("PRAGMA cache_size=-65536")     # 256MB page cache for scans
    conn.execute("PRAGMA temp_store=MEMORY")     # temp sorts in RAM
    conn.execute("PRAGMA mmap_size=134217728")   # 128MB mmap reads
    conn.executescript(SCHEMA)

    try:
        conn.execute("ALTER TABLE prices ADD COLUMN adjustment REAL")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE securities_master ADD COLUMN "table" TEXT')
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
