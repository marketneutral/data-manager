"""The store's tables. Every fact table has a key, values, source, added_in and superseded_in."""
import sqlite3
from pathlib import Path

from . import DEFAULT_STORE

# table -> (key columns, value columns). Every fact table also has source, added_in, superseded_in.
TABLES = {
    "securities": (["security_id"],
                   ["ticker", "name", "asset_class", "category", "exchange", "sector", "industry", "famaindustry",
                    "sicsector", "sicindustry", "isdelisted", "first_price_date", "last_price_date", "cusips", "figi"]),
    "prices": (["security_id", "date"],
               ["open_traded", "high_traded", "low_traded", "close_traded", "volume_traded"]),
    "splits": (["security_id", "ex_date"], ["ratio", "how"]),
    "dividends": (["security_id", "ex_date"], ["amount_traded"]),
    "other_adjustments": (["security_id", "ex_date"], ["factor", "kind"]),
    "shares": (["security_id", "filing_date", "dimension", "report_period"], ["shares_filed", "field"]),
}

TYPES = {
    "security_id": "TEXT", "date": "TEXT", "ex_date": "TEXT", "filing_date": "TEXT", "report_period": "TEXT",
    "dimension": "TEXT", "ticker": "TEXT", "name": "TEXT", "asset_class": "TEXT", "category": "TEXT",
    "exchange": "TEXT", "sector": "TEXT", "industry": "TEXT", "famaindustry": "TEXT", "sicsector": "TEXT",
    "sicindustry": "TEXT", "isdelisted": "TEXT", "first_price_date": "TEXT", "last_price_date": "TEXT",
    "cusips": "TEXT", "figi": "TEXT", "how": "TEXT", "kind": "TEXT", "field": "TEXT",
}


def _ddl(table):
    key, vals = TABLES[table]
    cols = [f"{c} {TYPES.get(c, 'REAL')}" for c in key + vals]
    cols += ["source TEXT NOT NULL", "added_in INTEGER NOT NULL", "superseded_in INTEGER"]
    pk = ", ".join(key + ["added_in"])
    k = ", ".join(key)
    return [f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(cols)}, PRIMARY KEY ({pk}))",
            f"CREATE INDEX IF NOT EXISTS {table}_current ON {table} ({k}) WHERE superseded_in IS NULL",
            f"CREATE INDEX IF NOT EXISTS {table}_loads ON {table} (added_in, superseded_in)"]


LOADS = """CREATE TABLE IF NOT EXISTS loads (
    load_id     INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    mode        TEXT NOT NULL,          -- full | increment
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    data_through TEXT,                  -- the latest price date in this load
    files       TEXT,                   -- JSON: name -> {path, size, sha256, modified}
    status      TEXT NOT NULL,          -- staged | accepted | quarantined | rejected
    summary     TEXT,                   -- JSON: counts per table (added, changed, missing) and the gate's findings
    reviewed_by TEXT,
    note        TEXT
)"""


def connect(path=None, readonly=False):
    path = Path(path or DEFAULT_STORE).expanduser()
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        ensure(conn)
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA cache_size=-262144")
    conn.execute("PRAGMA mmap_size=1073741824")
    return conn


def ensure(conn):
    conn.execute(LOADS)
    for t in TABLES:
        for s in _ddl(t):
            conn.execute(s)
    conn.commit()
