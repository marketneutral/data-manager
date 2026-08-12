"""Tests for data_manager.db — SQLite storage layer."""

import sqlite3

from data_manager import db

TABLES = {
    "universe",
    "prices",
    "classifications",
    "snapshots",
    "quarterly_statements",
    "ratios",
    "fundamentals",
    "descriptions",
    "french_factors",
}


def test_load_descriptions_stores_rows(conn, tmp_path):
    """The vendor field-dictionary table loads into `descriptions`."""
    import io, zipfile
    from data_manager import bulkload
    csv_ = ("table,indicator,isfilter,isprimarykey,title,description,unittype\n"
            "SF1,revenue,N,N,Revenues,The amount of Revenue recognised,currency\n"
            "SEP,close,N,N,Close Price - Split Adjusted,The official exchange close price,text\n")
    p = tmp_path / "descriptions.csv.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("descriptions.csv", csv_)
    n = bulkload.load_descriptions(str(p), conn)
    assert n == 2
    rows = conn.execute(
        "SELECT table_name, indicator, title FROM descriptions ORDER BY indicator").fetchall()
    assert {r["title"] for r in rows} == {"Revenues", "Close Price - Split Adjusted"}


def test_connect_creates_all_tables(db_path):
    conn = db.connect(db_path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    conn.close()


def test_connect_is_idempotent(db_path):
    db.connect(db_path).close()
    conn = db.connect(db_path)  # must not raise on an existing DB
    conn.close()


def test_connect_sets_wal_mode(db_path):
    conn = db.connect(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()


def test_connect_sets_busy_timeout(db_path):
    conn = db.connect(db_path)
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout == 20000
    conn.close()


def test_connect_migrates_legacy_schema(db_path):
    """A DB created before the enrichment columns must be migrated in place."""
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        CREATE TABLE universe (
            ticker TEXT PRIMARY KEY, name TEXT, source TEXT, added_at TEXT
        );
        CREATE TABLE prices (
            ticker TEXT, date TEXT, open REAL, high REAL, low REAL,
            close REAL, volume INTEGER, PRIMARY KEY (ticker, date)
        );
    """)
    raw.commit()
    raw.close()

    conn = db.connect(db_path)
    ucols = {r[1] for r in conn.execute("PRAGMA table_info(universe)")}
    assert {"figi", "cik", "sic", "sic_description", "lei"} <= ucols
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(prices)")}
    assert "adjustment" in pcols
    conn.close()


def test_connect_migration_preserves_existing_rows(db_path):
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        CREATE TABLE universe (
            ticker TEXT PRIMARY KEY, name TEXT, source TEXT, added_at TEXT
        );
        INSERT INTO universe VALUES ('AAPL', 'Apple', 'IWV', '2026-01-01');
    """)
    raw.commit()
    raw.close()

    conn = db.connect(db_path)
    row = conn.execute("SELECT ticker, name FROM universe").fetchone()
    assert row["ticker"] == "AAPL"
    assert row["name"] == "Apple"
    conn.close()


def test_prices_date_index_created():
    c = db.connect(":memory:")
    idx = [r[1] for r in c.execute("PRAGMA index_list('prices')")]
    assert "idx_prices_date" in idx
    c.close()


def test_default_db_expands_user_home():
    assert "~" not in str(db.DEFAULT_DB)
    assert db.DEFAULT_DB.is_absolute()


def test_optimize_db(tmp_path):
    from data_manager import dbopt
    p = tmp_path / "o.db"
    c = db.connect(p)
    c.execute("INSERT INTO prices (ticker, date, close, volume, adjustment) VALUES ('A','2026-01-01',5,100,1.0)")
    c.execute("INSERT INTO prices (ticker, date, close, volume, adjustment) VALUES ('A','2026-01-02',6,100,1.0)")
    c.commit()
    c.close()
    c = db.connect(p)
    r = dbopt.optimize_db(c, backup_path=str(tmp_path / "o.bak.db"), vacuum=True, quick=True)
    assert r["integrity"] == "ok"
    assert r["backup"].endswith("o.bak.db")
    assert "idx_prices_date" in r["indexes"]
    c.close()


def test_pit_history_profile_is_latest_quote_not_run_start(tmp_path):
    """universe_pit profile (price/mcap/dvol) on member day D must be the most
    recent valid quote as of D, not the run-start quote (bug fixed 2026-08-12:
    a continuous 1998->now run used to carry its opening quote forever)."""
    from data_manager import db
    from data_manager.universe import build_universe_pit_history

    c = db.connect(tmp_path / "pit.db")
    days = ["1998-01-%02d" % d for d in (2, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 20)]
    base = dict(category="Domestic Common Stock", exchange="NYSE", isdelisted="N",
                sector="Technology", industry="Software", firstpricedate="1998-01-02",
                lastpricedate="1998-01-20", name="T", cusips="", siccode="", sicsector="",
                sicindustry="", figi="", famaindustry="", scalemarketcap="", scalerevenue="",
                relatedtickers="", currency="USD", location="", firstadded="",
                firstquarter="", lastquarter="", secfilings="", companysite="",
                lastupdated="", permaticker="", table="stocks")
    def add_stock(t):
        c.execute('INSERT OR REPLACE INTO securities_master (ticker, category, exchange,'
                  ' isdelisted, sector, industry, firstpricedate, lastpricedate, name,'
                  ' permaticker, "table") VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  (t, base["category"], base["exchange"], base["isdelisted"],
                   base["sector"], base["industry"], "1998-01-02", "1998-01-20", "T",
                   t, "stocks"))
    def add_price(t, d, close, volume=1_000_000):
        c.execute("INSERT OR REPLACE INTO prices (ticker,date,close,volume,adjustment)"
                  " VALUES (?,?,?,?,1.0)", (t, d, close, volume))
    def add_shares(t, asof, n=10_000_000):
        c.execute("INSERT OR REPLACE INTO sf1 (ticker,dimension,date,reportperiod,shareswa)"
                  " VALUES (?,?,?,?,?)", (t, "ARQ", asof, asof, n))

    # CONT: continuous quotes, price climbing 50, 50.5, ...
    add_stock("CONT")
    add_shares("CONT", "1998-01-02")
    for i, d in enumerate(days):
        add_price("CONT", d, 50.0 + 0.5 * i)
    # GAP: quote run 1 (05..09), then a >10-calendar-day hole, then run 2 (26..30)
    add_stock("GAP")
    add_shares("GAP", "1998-01-02")
    for d, v in [("1998-01-05", 40.0), ("1998-01-06", 41.0), ("1998-01-07", 42.0),
                 ("1998-01-08", 43.0), ("1998-01-09", 44.0)]:
        add_price("GAP", d, v)
    for i, d in enumerate(["1998-01-26", "1998-01-27", "1998-01-28", "1998-01-29", "1998-01-30"]):
        add_price("GAP", d, 50.0 + i)
    c.commit()

    n = build_universe_pit_history(
        c, min_price=1.0, min_mcap=100_000_000, min_dvol=1_000_000,
        lookback=5, min_dvol_days=3, max_quote_age=10, types=("Domestic Common Stock",))
    assert n > 0

    # CONT: profile refreshes mid-run (a rising-price stock can't keep its opening quote)
    first = c.execute("SELECT price, mcap FROM universe_pit WHERE ticker='CONT' AND as_of='1998-01-06'").fetchone()
    mid = c.execute("SELECT price, mcap FROM universe_pit WHERE ticker='CONT' AND as_of='1998-01-15'").fetchone()
    lastd = c.execute("SELECT price, mcap FROM universe_pit WHERE ticker='CONT' AND as_of='1998-01-20'").fetchone()
    assert first[0] == 51.0 and abs(first[1] - 51.0 * 10_000_000) < 1  # 01-06 close
    assert mid[0] == 54.5 and abs(mid[1] - 54.5 * 10_000_000) < 1      # 01-15 close
    assert lastd[0] == 55.5 and abs(lastd[1] - 55.5 * 10_000_000) < 1  # 01-20 close

    # GAP: membership breaks across the hole; stale-day profile = last quote of run 1
    none = c.execute("SELECT COUNT(*) FROM universe_pit WHERE ticker='GAP' AND as_of='1998-01-20'").fetchone()[0]
    stale = c.execute("SELECT price FROM universe_pit WHERE ticker='GAP' AND as_of='1998-01-16'").fetchone()
    back = c.execute("SELECT price FROM universe_pit WHERE ticker='GAP' AND as_of='1998-01-28'").fetchone()
    assert none == 0
    assert stale[0] == 44.0    # last quote of run 1 (01-09), carried into the tail
    assert back[0] == 52.0     # run 2 profile reflects its own quotes
    c.close()
