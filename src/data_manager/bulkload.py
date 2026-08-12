"""Load Sharadar bulk-download zips into the data-manager SQLite DB.
Bulk endpoint: https://api.sharadar.com/v1.0/data/<table>?api_key=KEY&years=full
returns a redirect to a pre-prepared, time-limited .csv.zip (sharadar.com/docs/bulk,
discovered 2026-08-11). Verified semantics:
  - tickers zip uses table codes SEP=stocks, SFP=funds
  - prices (stocks/funds): OHLC split-adjusted, closeunadj as-traded, bulk
    volume is AS-TRADED (contrary to the FAQ text); depth 1998-> on this key
    (stocks full = 46.3M rows, complete extent even for years=full)
  - fundamentals zip: report date is `datekey`; SIX dimensions incl. TTM:
    ARY/MRY/ARQ/MRQ + ART/MRT
  - metrics zip carries history (rows back to 1997-12-31 for some tickers)
"""
import zipfile, io, csv, sqlite3, json, zlib, os, time

TABLE_MAP = {"SEP": "stocks", "SFP": "funds"}
MASTER_COLS = ["permaticker","ticker","name","exchange","isdelisted","category","cusips",
               "siccode","sicsector","sicindustry","figi","famaindustry","sector","industry",
               "scalemarketcap","scalerevenue","relatedtickers","currency","location",
               "firstadded","firstpricedate","lastpricedate","firstquarter","lastquarter",
               "secfilings","companysite","lastupdated","table"]

def stream_csv(path):
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            yield from csv.DictReader(io.TextIOWrapper(f))

def num(v):
    try:
        if v is None or v in ("", "N/A"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None

def load_tickers(path, conn):
    n = 0
    cols = ", ".join('"table"' if c == "table" else c for c in MASTER_COLS)
    for r in stream_csv(path):
        t = TABLE_MAP.get(r.get("table"))
        if not t:
            continue
        r = dict(r); r["table"] = t
        conn.execute(f"INSERT OR REPLACE INTO securities_master ({cols}) VALUES ({','.join('?'*len(MASTER_COLS))})",
                     tuple(r.get(c) for c in MASTER_COLS))
        n += 1
        if n % 20000 == 0:
            conn.commit()
    conn.commit()
    return n

def load_actions(path, conn):
    n = 0
    rows = []
    for r in stream_csv(path):
        rows.append((r.get("ticker"), r.get("date"), r.get("action"), r.get("name"),
                     r.get("value"), r.get("contraticker"), r.get("contraname")))
        n += 1
        if len(rows) >= 100000:
            conn.executemany("INSERT OR REPLACE INTO corporate_actions (ticker,date,action,name,value,contraticker,contraname) VALUES (?,?,?,?,?,?,?)", rows)
            rows = []; conn.commit()
    if rows:
        conn.executemany("INSERT OR REPLACE INTO corporate_actions (ticker,date,action,name,value,contraticker,contraname) VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return n

def load_metrics(path, conn):
    n = 0; rows = []
    def push(r):
        conn.execute("INSERT OR REPLACE INTO metrics (ticker, as_of, price, beta1y, beta5y, ma50d, ma200d, high52w, low52w, return1y, return5y, returnytd, volume, volumeavg1m, volumeavg3m, dividendyieldtrailing, dividendyieldforward, high5y, low5y) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (r.get("ticker"), r.get("date"), num(r.get("price")), num(r.get("beta1y")), num(r.get("beta5y")),
                      num(r.get("ma50d")), num(r.get("ma200d")), num(r.get("high52w")), num(r.get("low52w")),
                      num(r.get("return1y")), num(r.get("return5y")), num(r.get("returnytd")), num(r.get("volume")),
                      num(r.get("volumeavg1m")), num(r.get("volumeavg3m")), num(r.get("dividendyieldtrailing")),
                      num(r.get("dividendyieldforward")), num(r.get("high5y")), num(r.get("low5y"))))
    for r in stream_csv(path):
        push(r); n += 1
        if n % 50000 == 0:
            conn.commit()
    conn.commit()
    return n

def load_sp500(path, conn):
    n = 0; rows = []
    for r in stream_csv(path):
        rows.append((r.get("ticker"), r.get("date"), r.get("action"), r.get("name"),
                     r.get("contraticker"), r.get("contraname"), r.get("note")))
        n += 1
        if len(rows) >= 50000:
            conn.executemany("INSERT OR REPLACE INTO sp500_membership (ticker,date,action,name,contraticker,contraname,note) VALUES (?,?,?,?,?,?,?)", rows)
            rows = []; conn.commit()
    if rows:
        conn.executemany("INSERT OR REPLACE INTO sp500_membership (ticker,date,action,name,contraticker,contraname,note) VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return n

def parse_price_row(r):
    """bulk stocks/funds row -> (ticker, date, o,h,l,c,unadj, adj) as-traded mapping"""
    c_split = num(r.get("close"))
    c_unadj = num(r.get("closeunadj"))
    if c_unadj is None:
        c_unadj, factor = c_split, 1.0
    elif c_split:
        factor = c_unadj / c_split
    else:
        factor = 1.0
    adj_full = num(r.get("closeadj"))
    adjustment = (adj_full / c_unadj) if (adj_full and c_unadj) else 1.0
    vol = num(r.get("volume"))
    o, h, l = num(r.get("open")), num(r.get("high")), num(r.get("low"))
    return (r.get("ticker"), r.get("date"),
            o*factor if o is not None else None, h*factor if h is not None else None,
            l*factor if l is not None else None, c_unadj,
            int(vol) if vol is not None else None, adjustment)  # bulk volume is AS-TRADED

def load_prices(path, conn, commit_every=200000):
    n = 0; rows = []
    def flush():
        nonlocal rows
        conn.executemany("INSERT OR REPLACE INTO prices (ticker,date,open,high,low,close,volume,adjustment) VALUES (?,?,?,?,?,?,?,?)", rows)
        rows = []
    for r in stream_csv(path):
        rows.append(parse_price_row(r))
        n += 1
        if len(rows) >= commit_every:
            flush(); conn.commit()
    flush(); conn.commit()
    return n

def load_descriptions(path, conn, commit_every=5000):
    """Sharadar's field dictionary (one row per table+indicator): the
    authoritative column definitions for every vendor table. 7 cols:
    table, indicator, isfilter, isprimarykey, title, description, unittype."""
    n = 0; rows = []
    def flush():
        nonlocal rows
        conn.executemany(
            "INSERT OR REPLACE INTO descriptions "
            "(table_name, indicator, isfilter, isprimarykey, title, description, unittype) "
            "VALUES (?,?,?,?,?,?,?)", rows)
        rows = []
    for r in stream_csv(path):
        rows.append((r.get("table"), r.get("indicator"), r.get("isfilter"),
                     r.get("isprimarykey"), r.get("title"), r.get("description"),
                     r.get("unittype")))
        n += 1
        if len(rows) >= commit_every:
            flush(); conn.commit()
    flush(); conn.commit()
    return n


def load_fundamentals(path, conn, commit_every=100000):
    """Load the full SF1 mirror: ALL 112 vendor fields as native columns
    (no blob). `date` = vendor `datekey`; the 105 indicators are REAL."""
    from .db import SF1_INDICATORS
    cols = ",".join(SF1_INDICATORS)
    marks = ",".join("?" * len(SF1_INDICATORS))
    n = 0; rows = []
    def flush():
        nonlocal rows
        conn.executemany(
            "INSERT OR REPLACE INTO sf1 "
            "(ticker,dimension,date,reportperiod,fiscalperiod,calendardate,lastupdated,"
            + cols + ") VALUES (?,?,?,?,?,?,?," + marks + ")", rows)
        rows = []
    for r in stream_csv(path):
        rows.append((r.get("ticker"), r.get("dimension"), r.get("datekey") or r.get("date"),
                     r.get("reportperiod"), r.get("fiscalperiod"), r.get("calendardate"),
                     r.get("lastupdated"), *[num(r.get(c)) for c in SF1_INDICATORS]))
        n += 1
        if len(rows) >= commit_every:
            flush(); conn.commit()
    flush(); conn.commit()
    return n


load_stocks = load_prices
load_funds = load_prices


# --------------------------------------------------------------------------
# Ken French daily factor returns (mba.tuck.dartmouth.edu, US daily files)
# --------------------------------------------------------------------------

def _french_num(v):
    """Number from a French CSV cell; vendor missing sentinels -> None."""
    v = v.strip()
    if v in ("", "-99.99", "-999", "-99.00"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _french_iso(d8: str) -> str:
    """'YYYYMMDD' -> 'YYYY-MM-DD' (repo-wide date convention)."""
    return f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"


def load_french_factor_file(path, conn, cols, vcols, commit_every=50000):
    """Load ONE Ken French daily-factor CSV zip into `french_factors`.

    Format (all five files): comment preamble lines, a header line whose
    first field is empty (e.g. `,Mkt-RF,SMB,HML,RF`), then data rows
    `YYYYMMDD, 0.09, -0.25, ...`. Values are PERCENT per day; the vendor
    -99.99 missing sentinel becomes NULL. Columns map positionally to
    `cols` (db column names); `vcols` is the vendor header used for a
    sanity warning on format drift, never for the mapping.
    """
    import io
    import csv
    import zipfile
    n = 0
    rows = []
    marks = ",".join("?" * len(cols))
    # per-column upsert: the wide table is filled from FIVE source files, so a
    # plain INSERT OR REPLACE would delete the row and NULL out the other
    # files' columns. Last file loaded wins for its own columns only.
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols)

    def flush():
        nonlocal rows
        conn.executemany(
            "INSERT INTO french_factors (date," + ",".join(cols) + ")"
            " VALUES (?," + marks + ")"
            " ON CONFLICT(date) DO UPDATE SET " + updates, rows)
        rows = []

    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            for line in csv.reader(io.TextIOWrapper(f)):
                if not line:
                    continue
                first = line[0].strip()
                if first == "" and len(line) > 1:          # header line
                    got = [c.strip() for c in line[1:]]
                    if got[:len(vcols)] != vcols:
                        print(f"[french] header drift: expected {vcols}, got {got}",
                              flush=True)
                    continue
                if not (len(first) == 8 and first.isdigit()):  # preamble/notes
                    continue
                vals = [_french_num(c) for c in line[1:1 + len(cols)]]
                rows.append((_french_iso(first), *vals))
                n += 1
                if len(rows) >= commit_every:
                    flush()
                    conn.commit()
    flush()
    conn.commit()
    return n
