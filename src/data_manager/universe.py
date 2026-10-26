"""Orchestration: fetch data from providers and store into SQLite."""

import datetime as dt
import os
import time

from . import db
from .providers.ishares import ISharesProvider
from .providers.financialdatasets import FinancialDatasetsProvider



# --------------------------------------------------------------------------
# Sharadar extras: securities master + corporate actions (delisted-aware)
# --------------------------------------------------------------------------

_MASTER_COLS = ["permaticker", "ticker", "name", "exchange", "isdelisted",
                "category", "cusips", "siccode", "sicsector", "sicindustry",
                "figi", "famaindustry", "sector", "industry", "scalemarketcap",
                "scalerevenue", "relatedtickers", "currency", "location",
                "firstadded", "firstpricedate", "lastpricedate",
                "firstquarter", "lastquarter", "secfilings", "companysite",
                "lastupdated"]


def update_master(tickers, conn=None, provider=None, pace: float = 0.3) -> int:
    """Mirror the Sharadar securities-master rows (tickers table)."""
    from .providers.sharadar import _fetch
    conn = conn or db.connect()
    total = 0
    for t in tickers:
        if conn.execute("SELECT 1 FROM securities_master WHERE permaticker IS NOT NULL "
                        "AND ticker=?", (t,)).fetchone():
            continue
        rows = _fetch("tickers", ticker=t)
        if not rows:
            continue
        r = rows[0]
        conn.execute(
            f"INSERT OR REPLACE INTO securities_master ({', '.join(_MASTER_COLS)}) "
            f"VALUES ({', '.join('?' * len(_MASTER_COLS))})",
            tuple(r.get(c) for c in _MASTER_COLS))
        total += 1
        conn.commit()
        time.sleep(pace)
    return total


def update_actions(tickers, conn=None, provider=None, pace: float = 0.3) -> int:
    """Mirror the Sharadar corporate-actions rows (splits/dividends/delists)."""
    from .providers.sharadar import _fetch
    conn = conn or db.connect()
    total = 0
    for t in tickers:
        rows = _fetch("actions", ticker=t)
        if not rows:
            continue
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO corporate_actions "
                "(ticker, date, action, name, value, contraticker, contraname) "
                "VALUES (?,?,?,?,?,?,?)",
                (t, r.get("date"), r.get("action"), r.get("name"),
                 r.get("value"), r.get("contraticker"), r.get("contraname")))
def update_sp500(conn=None, pace: float = 0.25) -> int:
    """Mirror Sharadar S&P500 membership.

    The unscoped sp500 pull is capped to ~1 year by the API; per-ticker pulls
    return full membership history per member (e.g. AAPL back to 1982-11-30).
    Stores both into sp500_membership (action: current|historical|added|removed).
    """
    from .providers.sharadar import _fetch
    conn = conn or db.connect()
    total = 0
    rows = _fetch("sp500")
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO sp500_membership "
            "(ticker, date, action, name, contraticker, contraname, note) "
            "VALUES (?,?,?,?,?,?,?)",
            (r.get("ticker"), r.get("date"), r.get("action"), r.get("name"),
             r.get("contraticker"), r.get("contraname"), r.get("note")))
    total += len(rows)
    conn.commit()
    members = sorted({r["ticker"] for r in rows if r.get("action") == "current"})
    for t in members:
        deep = _fetch("sp500", ticker=t)
        if not deep:
            continue
        for r in deep:
            conn.execute(
                "INSERT OR REPLACE INTO sp500_membership "
                "(ticker, date, action, name, contraticker, contraname, note) "
                "VALUES (?,?,?,?,?,?,?)",
                (t, r.get("date"), r.get("action"), r.get("name"),
                 r.get("contraticker"), r.get("contraname"), r.get("note")))
        total += len(deep)
        conn.commit()
        time.sleep(pace)
    return total


def _default_data_provider():
    """Sharadar is THE provider (FMP removed 2026-08-11; fresh start)."""
    from .providers.sharadar import SharadarProvider
    return SharadarProvider()


def update_universe(conn=None, provider=None) -> int:
    """Fetch R3000 constituents and store them in the universe table.

    Defaults to the free ISharesProvider (IWV holdings). Stores ticker, name,
    source, and — if the provider returns it — the sector classification.

    Returns the number of tickers stored.
    """
    conn = conn or db.connect()
    provider = provider or ISharesProvider()
    constituents = provider.get_universe()
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat()
    # Upsert that PRESERVES enrichment columns (figi/cik/sic/lei): a bare
    # INSERT OR REPLACE would wipe them on every universe refresh.
    conn.executemany(
        "INSERT INTO universe (ticker, name, source, added_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET "
        "name=excluded.name, source=excluded.source, added_at=excluded.added_at",
        [(c["ticker"], c.get("name"), c.get("source"), now) for c in constituents],
    )
    # If the provider carries sector info (e.g. iShares), store classifications too.
    if any(c.get("sector") for c in constituents):
        today = dt.date.today().isoformat()
        for c in constituents:
            if c.get("sector"):
                # Preserve any existing industry (update-classifications fills it).
                conn.execute(
                    "INSERT INTO classifications (ticker, sector, industry, as_of) "
                    "VALUES (?, ?, NULL, ?) ON CONFLICT(ticker) DO UPDATE SET "
                    "sector=excluded.sector, as_of=excluded.as_of",
                    (c["ticker"], c["sector"], today),
                )
    # Record a snapshot: pull time + the holdings' own as_of date + row count.
    as_of = getattr(provider, "get_as_of_date", None)
    as_of_date = as_of() if callable(as_of) else None
    conn.execute(
        "INSERT INTO snapshots (source, pulled_at, as_of, row_count) VALUES (?, ?, ?, ?)",
        (provider.name, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(),
         as_of_date, len(constituents)),
    )
    conn.commit()
    return len(constituents)


def update_prices(tickers, start, end, conn=None, provider=None, pace: float = 0.25,
                 force: bool = False) -> int:
    """Fetch daily OHLCV prices for the given tickers and store them.

    Resumable: tickers whose stored data already covers `end` are skipped.
    Resilient: per-ticker errors are logged and skipped (rate limits, delisted).
    Pacer: `pace` seconds between tickers to stay under FMP rate limits.
    Returns the number of price rows stored.
    """
    conn = conn or db.connect()
    provider = provider or _default_data_provider()
    total = 0
    for ticker in tickers:
        row = conn.execute("SELECT MAX(date) FROM prices WHERE ticker=?", (ticker,)).fetchone()
        if not force and row and row[0] and row[0] >= end:
            continue  # already fully covered -> resume cheaply
        try:
            rows = provider.get_prices(ticker, start, end)
        except Exception as exc:
            print(f"[prices] {ticker}: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(3)
            continue
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO prices "
                "(ticker, date, open, high, low, close, volume, adjustment) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(ticker, r["date"], r["open"], r["high"], r["low"],
                  r["close"], r["volume"], r["adjustment"]) for r in rows],
            )
            total += len(rows)
        conn.commit()  # incremental: progress visible, survives interruption
        time.sleep(pace)
    return total


def update_classifications(tickers, conn=None, provider=None, force: bool = False) -> int:
    """Fetch sector/industry for the given tickers and store them."""
    conn = conn or db.connect()
    provider = provider or _default_data_provider()
    count = 0
    for ticker in tickers:
        have = conn.execute(
            "SELECT industry FROM classifications WHERE ticker=?", (ticker,)).fetchone()
        if not force and have and have[0]:
            continue  # already has industry -> resumable
        try:
            c = provider.get_classification(ticker)
        except Exception as exc:
            print(f"[classifications] {ticker}: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(3)
            continue
        conn.execute(
            "INSERT OR REPLACE INTO classifications (ticker, sector, industry, as_of) "
            "VALUES (?, ?, ?, ?)",
            (ticker, c.get("sector"), c.get("industry"), dt.date.today().isoformat()),
        )
        count += 1
        conn.commit()  # incremental per ticker
    return count


def update_fundamentals(tickers, conn=None, provider=None, pace: float = 0.5,
                        force: bool = False) -> int:
    """Fetch annual fundamentals (Piotroski F-Score) from Sharadar SF1.

    Resumable: tickers already having any fundamentals rows are skipped unless
    force=True. Resilient: per-ticker errors are logged and skipped.
    Returns number of fundamental rows stored.
    """
    conn = conn or db.connect()
    provider = provider or _default_data_provider()
    total = 0
    for ticker in tickers:
        row = conn.execute("SELECT COUNT(*) FROM fundamentals WHERE ticker=?", (ticker,)).fetchone()
        if not force and row and row[0]:
            continue
        try:
            rows = provider.get_fundamentals(ticker)
        except Exception as exc:
            print(f"[fundamentals] {ticker}: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(3)
            continue
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO fundamentals "
                "(ticker, fiscal_year, roa, cfo, d_roa, accruals, d_leverage, "
                " d_liquidity, equity_issuance, d_gross_margin, d_asset_turnover, f_score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, r["fiscal_year"], r["roa"], r["cfo"], r["d_roa"],
                 r["accruals"], r["d_leverage"], r["d_liquidity"],
                 r["equity_issuance"], r["d_gross_margin"], r["d_asset_turnover"],
                 r["f_score"]),
            )
            total += 1
        conn.commit()  # incremental per ticker
        time.sleep(pace)
    return total


def adjusted_prices(ticker: str, start: str | None = None, end: str | None = None,
                    conn=None, asof: str | None = None) -> list[dict]:
    """Return as-of OHLCV with query-time adjustments applied.

    adjustment is the per-day factor (closeadj / close: splits + dividends,
    normalized to 1.0 on the latest quote). Adjusted field = raw field *
    adjustment, so open/high/low/close can all be adjusted consistently.
    Volume is left as traded. Returns dicts keyed
    date/open/high/low/close/adjusted_open/.../adjustment.

    asof: point-in-time anchor. adjusted_* then equal raw * adjustment /
    adjustment(asof): the total-return series rebased so the as-of date
    equals its as-traded price and only corporate actions known by `asof`
    affect the level (no future events). Ratios of adjusted prices between
    two dates <= asof are IDENTICAL with or without asof (the rebase scale
    cancels), so plain adjusted_prices() is already PIT-safe for returns.
    Pass asof only when you need the LEVEL (charts, TR indexes, drawdowns,
    normalized series) or when mixing levels across different as-of dates.
    """
    conn = conn or db.connect()
    q = "SELECT date, open, high, low, close, volume, adjustment FROM prices WHERE ticker=?"
    args = [ticker]
    if start:
        q += " AND date>=?"; args.append(start)
    if end:
        q += " AND date<=?"; args.append(end)
    q += " ORDER BY date"
    rows = conn.execute(q, args).fetchall()
    scale = 1.0
    if asof is not None:
        a = conn.execute(
            "SELECT adjustment FROM prices WHERE ticker=? AND date<=? "
            "ORDER BY date DESC LIMIT 1", (ticker, asof)).fetchone()
        if a is None or a["adjustment"] is None:
            raise ValueError(f"no adjustment on or before {asof} for {ticker}")
        scale = a["adjustment"]
    out = []
    for r in rows:
        adj = (r["adjustment"] if r["adjustment"] is not None else 1.0) / scale
        out.append({
            "date": r["date"],
            "open": r["open"], "high": r["high"], "low": r["low"],
            "close": r["close"], "volume": r["volume"], "adjustment": adj,
            "adjusted_open": (r["open"] * adj) if r["open"] is not None else None,
            "adjusted_high": (r["high"] * adj) if r["high"] is not None else None,
            "adjusted_low": (r["low"] * adj) if r["low"] is not None else None,
            "adjusted_close": (r["close"] * adj) if r["close"] is not None else None,
        })
    return out


_RATIO_COLS = ["trailing_pe", "forward_pe", "price_to_book", "price_to_sales", "roe", "roa",
               "net_margin", "gross_margin", "operating_margin", "debt_to_equity",
               "current_ratio", "dividend_yield", "market_cap", "enterprise_value",
               "ev_to_ebitda", "beta", "shares_outstanding"]


def update_ratios(tickers, conn=None, provider=None, pace: float = 0.5,
                   force: bool = False) -> int:
    """Snapshot point-in-time fundamental ratios per ticker (Sharadar SF1).

    Resumable: skips tickers already snapshotted today unless force=True.
    Resilient + paced. Returns count of ratio rows stored.
    """
    conn = conn or db.connect()
    provider = provider or _default_data_provider()
    as_of = dt.date.today().isoformat()
    total = 0
    for ticker in tickers:
        have = conn.execute(
            "SELECT COUNT(*) FROM ratios WHERE ticker=? AND as_of=?", (ticker, as_of)).fetchone()
        if not force and have and have[0]:
            continue
        try:
            r = provider.get_ratios(ticker)
        except Exception as exc:
            print(f"[ratios] {ticker}: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(3)
            continue
        if not r:
            continue
        cols = ", ".join(_RATIO_COLS)
        marks = ", ".join(["?"] * len(_RATIO_COLS))
        conn.execute(
            f"INSERT OR REPLACE INTO ratios (ticker, as_of, {cols}) VALUES (?, ?, {marks})",
            (ticker, as_of) + tuple(r.get(c) for c in _RATIO_COLS),
        )
        total += 1
        conn.commit()
        time.sleep(pace)
    return total


_Q_COLS = ["net_income","revenue","gross_profit","operating_cash_flow","total_assets",
           "total_liabilities","current_assets","current_liabilities","shares_out","roa","cfo"]


def update_quarterly(tickers, conn=None, provider=None, pace: float = 0.2,
                     force: bool = False) -> int:
    """Fetch quarterly statements (Sharadar SF1). Resumable: skips tickers with rows unless force=True."""
    conn = conn or db.connect()
    provider = provider or _default_data_provider()
    total = 0
    for ticker in tickers:
        have = conn.execute("SELECT COUNT(*) FROM quarterly_statements WHERE ticker=?", (ticker,)).fetchone()[0]
        if not force and have:
            continue
        try:
            rows = provider.get_quarterly(ticker)
        except Exception as exc:
            print(f"[quarterly] {ticker}: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(2)
            continue
        cols = ", ".join(_Q_COLS); marks = ", ".join(["?"] * len(_Q_COLS))
        conn.executemany(
            f"INSERT OR REPLACE INTO quarterly_statements (ticker, period, {cols}) VALUES (?, ?, {marks})",
            [(ticker, r.get("period")) + tuple(r.get(c) for c in _Q_COLS) for r in rows],
        )
        total += len(rows)
        conn.commit()
        time.sleep(pace)
    return total


def universe_tickers(conn=None) -> list[str]:
    """Return the list of tickers currently in the universe table."""
    conn = conn or db.connect()
    rows = conn.execute("SELECT ticker FROM universe ORDER BY ticker").fetchall()
    return [r["ticker"] for r in rows]

# ============================================================================
# WAREHOUSE MODE (2026-08-11): ALL US equities PIT from Sharadar.
# Whole-table pulls where the API allows; size-aware batched pulls elsewhere.
# ============================================================================

_MASTER_ALL = _MASTER_COLS + ["table"]
_SF1_DIMS = ["ARY", "MRY", "ARQ", "MRQ"]


def master_stocks(conn, isdelisted=None) -> list[str]:
    """All stock tickers from the securities master (table='stocks').

    Honors DM_SHARD/DM_SHARDS env vars (parallel shards for the long jobs).
    """
    q = 'SELECT ticker FROM securities_master WHERE "table"=\'stocks\''
    if isdelisted is not None:
        q += f" AND isdelisted='{isdelisted}'"
    rows = conn.execute(q).fetchall()
    tickers = [r[0] for r in rows]
    shards = int(os.environ.get("DM_SHARDS", "1"))
    shard = int(os.environ.get("DM_SHARD", "0"))
    if shards > 1:
        tickers = [t for i, t in enumerate(tickers) if i % shards == shard]
    return tickers


def _page(table, limit: int, offset: int, **params) -> list[dict]:
    from .providers.sharadar import _fetch
    try:
        return _fetch(table, limit=str(limit), offset=str(offset), **params)
    except Exception:
        return []


def update_master_all(conn=None, pace: float = 0.2) -> int:
    """Whole-table mirror of the Sharadar tickers master (stocks + funds only)."""
    from .providers.sharadar import _fetch
    conn = conn or db.connect()
    total = 0
    offset = 0
    while True:
        rows = _fetch("tickers", limit="100000", offset=str(offset))
        if not rows:
            break
        keep = [r for r in rows if r.get("table") in ("stocks", "funds")]
        cols = ", ".join('"table"' if c == "table" else c for c in _MASTER_ALL)
        for r in keep:
            conn.execute(
                f"INSERT OR REPLACE INTO securities_master ({cols}) "
                f"VALUES ({', '.join('?' * len(_MASTER_ALL))})",
                tuple(r.get(c) for c in _MASTER_ALL))
        total += len(keep)
        conn.commit()
        if len(rows) < 100000:
            break
        offset += len(rows)
        time.sleep(pace)
    return total


def update_actions_all(conn=None, pace: float = 0.2) -> int:
    """Whole-table mirror of the Sharadar corporate-actions table."""
    from .providers.sharadar import _fetch
    conn = conn or db.connect()
    total = 0
    offset = 0
    while True:
        rows = _fetch("actions", limit="50000", offset=str(offset))
        if not rows:
            break
        conn.executemany(
            "INSERT OR REPLACE INTO corporate_actions "
            "(ticker, date, action, name, value, contraticker, contraname) "
            "VALUES (?,?,?,?,?,?,?)",
            [(r.get("ticker"), r.get("date"), r.get("action"), r.get("name"),
              r.get("value"), r.get("contraticker"), r.get("contraname")) for r in rows])
        total += len(rows)
        conn.commit()
        if len(rows) < 50000:
            break
        offset += len(rows)
        time.sleep(pace)
    return total


def update_metrics_all(conn=None, pace: float = 0.2) -> int:
    """Whole-table mirror of the Sharadar metrics snapshot (latest per ticker)."""
    from .providers.sharadar import _fetch
    conn = conn or db.connect()
    total = 0
    offset = 0
    while True:
        rows = _fetch("metrics", limit="50000", offset=str(offset))
        if not rows:
            break
        conn.executemany(
            "INSERT OR REPLACE INTO metrics "
            "(ticker, as_of, price, beta1y, beta5y, ma50d, ma200d, high52w, low52w,"
            " return1y, return5y, returnytd, volume, volumeavg1m, volumeavg3m,"
            " dividendyieldtrailing, dividendyieldforward, high5y, low5y) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.get("ticker"), r.get("date"),
              _numf(r.get("price")), _numf(r.get("beta1y")), _numf(r.get("beta5y")),
              _numf(r.get("ma50d")), _numf(r.get("ma200d")), _numf(r.get("high52w")),
              _numf(r.get("low52w")), _numf(r.get("return1y")), _numf(r.get("return5y")),
              _numf(r.get("returnytd")), _numf(r.get("volume")),
              _numf(r.get("volumeavg1m")), _numf(r.get("volumeavg3m")),
              _numf(r.get("dividendyieldtrailing")), _numf(r.get("dividendyieldforward")),
              _numf(r.get("high5y")), _numf(r.get("low5y"))) for r in rows])
        total += len(rows)
        conn.commit()
        if len(rows) < 50000:
            break
        offset += len(rows)
        time.sleep(pace)
    return total


def _numf(v):
    try:
        if v is None or v == "" or v == "N/A":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- SF1 full mirror, batched per dimension (ticker=batch supported) ----
# rough rows-per-ticker estimates per dimension (from live probes)
_EST = {"ARY": 38, "MRY": 40, "ARQ": 140, "MRQ": 145}


def _sf1_batches(tickers: list[str], dimension: str) -> list[list[str]]:
    """Batches for the fundamentals endpoint: server caps ~30 tickers/request."""
    n = 25
    return [tickers[i:i + n] for i in range(0, len(tickers), n)]


def update_sf1_all(conn=None, dimensions=None, pace: float = 0.15) -> int:
    """Full SF1 mirror (all dimensions, full history) via batched pulls.

    All 112 vendor fields are stored as native columns (no blob)."""
    from .providers.sharadar import _fetch
    from .db import SF1_INDICATORS
    conn = conn or db.connect()
    dims = dimensions or _SF1_DIMS
    total = 0
    tickers = master_stocks(conn)
    cols = ",".join(SF1_INDICATORS)
    marks = ",".join("?" * len(SF1_INDICATORS))
    sql = ("INSERT OR REPLACE INTO sf1 "
           "(ticker,dimension,date,reportperiod,fiscalperiod,calendardate,lastupdated,"
           + cols + ") VALUES (?,?,?,?,?,?,?," + marks + ")")
    for dim in dims:
        for batch in _sf1_batches(tickers, dim):
            rows = _fetch("fundamentals", ticker=",".join(batch), dimension=dim)
            if not rows:
                continue
            for r in rows:
                conn.execute(sql,
                    (r.get("ticker"), dim, r.get("datekey") or r.get("date"),
                     r.get("reportperiod"), r.get("fiscalperiod"), r.get("calendardate"),
                     r.get("lastupdated"), *[_numf(r.get(c)) for c in SF1_INDICATORS]))
            total += len(rows)
            conn.commit()
            time.sleep(pace)
    return total


def _hydrate_sf1(conn, ticker: str, dimension: str) -> list[dict]:
    """Full SF1 rows for (ticker, dimension) as vendor-keyed dicts.

    All 112 fields are native columns now; `date` is surfaced as `datekey`
    (the vendor name) for caller compatibility."""
    rows = conn.execute(
        "SELECT * FROM sf1 WHERE ticker=? AND dimension=? ORDER BY calendardate",
        (ticker, dimension)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["datekey"] = d.get("date")
        out.append(d)
    return out


def _price_batches(tickers: list[str], start: str, end: str, conn) -> list[list[str]]:
    """Batch by estimated rows so each response stays under the ~7MB cap."""
    est = {}
    for t in tickers:
        row = conn.execute(
            'SELECT firstpricedate, lastpricedate FROM securities_master WHERE ticker=?', (t,)).fetchone()
        first = (row[0] if row and row[0] else start)
        last = (row[1] if row and row[1] else end) or end
        lo, hi = max(first, start), min(last, end)
        est[t] = 0 if lo >= hi else max(1, int((dt.date.fromisoformat(hi) - dt.date.fromisoformat(lo)).days * 5 / 7))
    batches, cur, cur_n = [], [], 0
    for t in tickers:
        if cur_n + est[t] > 30000 or len(cur) >= 28:
            batches.append(cur); cur, cur_n = [], 0
        cur.append(t); cur_n += est[t]
    if cur:
        batches.append(cur)
    return batches


# the stocks endpoint truncates oversized responses at ~50,000 rows (newest
# kept); anything above that must be pulled in smaller batches and verified
_RESP_CAP = 50000


def _fetch_prices_batch(tickers, est, start, end, pace):
    """Fetch a price batch, halving until it fits under the 50k response cap."""
    from .providers.sharadar import _fetch, parse_prices
    est_n = sum(est[t] for t in tickers)
    if est_n > _RESP_CAP * 1.05:
        mid = len(tickers) // 2
        return (_fetch_prices_batch(tickers[:mid], est, start, end, pace) +
                _fetch_prices_batch(tickers[mid:], est, start, end, pace))
    rows = _fetch("stocks", ticker=",".join(tickers))
    if len(rows) >= _RESP_CAP - 10 and est_n > _RESP_CAP - 100:
        mid = len(tickers) // 2
        return (_fetch_prices_batch(tickers[:mid], est, start, end, pace) +
                _fetch_prices_batch(tickers[mid:], est, start, end, pace))
    return rows


def update_prices_all_stocks(conn=None, start: str = "1996-01-01", end: str = None,
                             pace: float = 0.2) -> int:
    """As-traded OHLCV for EVERY stock in the master (incl. delisted).

    NOTE: the stocks endpoint serves at most the last ~7,196 rows / ~28.6y per
    ticker (back to 1998) -- earlier firstpricedate metadata is not fetchable.
    Batches are halved under the ~50k-row response cap and every ticker's
    coverage is verified against the window estimate (short ones refetched
    individually).
    """
    from .providers.sharadar import _fetch, parse_prices
    conn = conn or db.connect()
    end = end or dt.date.today().isoformat()
    tickers = master_stocks(conn)
    est = {}
    for t in tickers:
        row = conn.execute(
            'SELECT firstpricedate, lastpricedate FROM securities_master WHERE ticker=?', (t,)).fetchone()
        first = (row[0] if row and row[0] else start)
        last = (row[1] if row and row[1] else end) or end
        lo, hi = max(first, start), min(last, end)
        est[t] = 0 if lo >= hi else max(1, int((dt.date.fromisoformat(hi) - dt.date.fromisoformat(lo)).days * 5 / 7))
    total = 0
    for batch in _price_batches(tickers, start, end, conn):
        rows = _fetch_prices_batch(batch, est, start, end, pace)
        counts: dict[str, int] = {}
        entries = parse_prices(rows, start, end)
        for e in entries:
            counts[e["ticker"]] = counts.get(e["ticker"], 0) + 1
        conn.executemany(
            "INSERT OR REPLACE INTO prices "
            "(ticker, date, open, high, low, close, volume, adjustment) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(e["ticker"], e["date"], e["open"], e["high"], e["low"],
              e["close"], e["volume"], e["adjustment"]) for e in entries])
        total += len(entries)
        conn.commit()
        # per-ticker coverage: a ticker with fewer rows than its window estimate
        # was truncated -> refetch it alone (single-ticker pulls are complete)
        for t in batch:
            want = min(est[t], 7196)
            if want > 50 and counts.get(t, 0) < want - 20:
                sub = parse_prices(_fetch("stocks", ticker=t), start, end)
                conn.executemany(
                    "INSERT OR REPLACE INTO prices "
                    "(ticker, date, open, high, low, close, volume, adjustment) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    [(t, e["date"], e["open"], e["high"], e["low"], e["close"], e["volume"], e["adjustment"])
                     for e in sub])
                total += len(sub)
                conn.commit()
                time.sleep(pace)
        time.sleep(pace)
    return total


# ---- local derivations from the sf1 mirror (zero remote requests) ----


def build_piotroski(conn=None) -> int:
    from .providers.sharadar import fscore_from_rows
    conn = conn or db.connect()
    total = 0
    for t in master_stocks(conn):
        rows = _hydrate_sf1(conn, t, "ARY")
        if not rows:
            continue
        fund = fscore_from_rows(rows)
        for f in fund:
            conn.execute(
                "INSERT OR REPLACE INTO fundamentals "
                "(ticker, fiscal_year, roa, cfo, d_roa, accruals, d_leverage,"
                " d_liquidity, equity_issuance, d_gross_margin, d_asset_turnover, f_score, filing_date)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (t, f["fiscal_year"], f.get("roa"), f.get("cfo"), f.get("d_roa"),
                 f.get("accruals"), f.get("d_leverage"), f.get("d_liquidity"),
                 f.get("equity_issuance"), f.get("d_gross_margin"),
                 f.get("d_asset_turnover"), f.get("f_score"), f.get("filing_date")))
        total += len(fund)
        conn.commit()
    return total


def build_quarterly(conn=None) -> int:
    from .providers.sharadar import quarterly_from_rows
    conn = conn or db.connect()
    total = 0
    for t in master_stocks(conn):
        rows = _hydrate_sf1(conn, t, "ARQ")
        if not rows:
            continue
        q = quarterly_from_rows(rows)
        for r in q:
            conn.execute(
                "INSERT OR REPLACE INTO quarterly_statements "
                "(ticker, period, net_income, revenue, gross_profit, operating_cash_flow,"
                " total_assets, total_liabilities, current_assets, current_liabilities,"
                " shares_out, roa, cfo, filing_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (t, r["period"], r["net_income"], r["revenue"], r["gross_profit"],
                 r["operating_cash_flow"], r["total_assets"], r["total_liabilities"],
                 r["current_assets"], r["current_liabilities"], r["shares_out"],
                 r["roa"], r["cfo"], r.get("filing_date")))
        total += len(q)
        conn.commit()
    return total


def build_ratios(conn=None) -> int:
    from .providers.sharadar import ratios_from_row
    conn = conn or db.connect()
    as_of = dt.date.today().isoformat()
    total = 0
    for t in master_stocks(conn):
        rows = _hydrate_sf1(conn, t, "MRY")
        if not rows:
            continue
        latest = sorted(rows, key=lambda r: str(r.get("calendardate", "")))[-1]
        r = ratios_from_row(latest)
        if not r:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO ratios "
            "(ticker, as_of, trailing_pe, forward_pe, price_to_book, price_to_sales,"
            " roe, roa, net_margin, gross_margin, operating_margin, debt_to_equity,"
            " current_ratio, dividend_yield, market_cap, enterprise_value,"
            " ev_to_ebitda, beta, shares_outstanding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t, as_of, r.get("trailing_pe"), r.get("forward_pe"), r.get("price_to_book"),
             r.get("price_to_sales"), r.get("roe"), r.get("roa"), r.get("net_margin"),
             r.get("gross_margin"), r.get("operating_margin"), r.get("debt_to_equity"),
             r.get("current_ratio"), r.get("dividend_yield"), r.get("market_cap"),
             r.get("enterprise_value"), r.get("ev_to_ebitda"), r.get("beta"),
             r.get("shares_outstanding")))
        total += 1
        conn.commit()
    return total


# ---- Split events ----
# corporate_actions mirrors every split row the vendor has ever served, and its key
# includes the value, so a refresh that re-serves an old split at more decimal
# places (0.33333 -> 0.333333333) or moves it to the next trading day adds a row
# beside the old one instead of replacing it. Every consumer multiplies split
# ratios, so a doubled row squares the factor. split_events is the one-row-per-
# split view the derived tables use:
#   * rows on the same ticker and date are one split; the most precise value wins,
#     or, when the values disagree by more than 1%, the one matching the price move;
#   * rows with the same ratio (within 1%) within SPLIT_CLUSTER_DAYS are one split
#     unless the as-traded close confirms each of them separately (vendor prices
#     sometimes carry the split twice, and the shares must agree with the prices).
# A row's price move is the last close before its first trading day over that
# day's close; it confirms ratio s when it is within 25% of s.
SPLIT_CLUSTER_DAYS = 10


def _split_move(conn, ticker, date):
    """(first trading day >= date, prev close / close) or (None, None) without quotes."""
    row = conn.execute("SELECT date, close FROM prices WHERE ticker=? AND date >= ? AND date <= date(?, '+7 days')"
                       " AND close > 0 ORDER BY date LIMIT 1", (ticker, date, date)).fetchone()
    if not row:
        return None, None
    prev = conn.execute("SELECT close FROM prices WHERE ticker=? AND date < ? AND date >= date(?, '-10 days')"
                        " AND close > 0 ORDER BY date DESC LIMIT 1", (ticker, row[0], row[0])).fetchone()
    return row[0], (prev[0] / row[1] if prev else None)


def _fit(move, ratio):
    import math
    return abs(math.log(move / ratio)) if move else math.inf


def split_events(conn, ticker):
    """[(date, ratio, raw_rows, rule)] for one ticker, oldest first; see the note above."""
    import math
    raw = conn.execute("SELECT date, value FROM corporate_actions WHERE ticker=? AND action='split'"
                       " AND CAST(value AS REAL) > 0 ORDER BY date", (ticker,)).fetchall()
    by_date = {}
    for d, v in raw:
        by_date.setdefault(d, []).append(v)
    events = []                                   # [date, ratio, raw_rows, rule]
    for d, vals in by_date.items():
        rs = [float(v) for v in vals]
        if len(vals) == 1:
            events.append([d, rs[0], 1, "single"])
        elif max(rs) / min(rs) <= 1.01:
            events.append([d, float(max(vals, key=lambda v: len(v.strip()))), len(vals), "same_date"])
        else:
            _, move = _split_move(conn, ticker, d)
            best = min(rs, key=lambda r: _fit(move, r)) if move else float(max(vals, key=lambda v: len(v.strip())))
            events.append([d, best, len(vals), "same_date_conflict"])
    out, i = [], 0
    while i < len(events):                        # cluster same-ratio rows within SPLIT_CLUSTER_DAYS
        j = i + 1
        while (j < len(events) and abs(math.log(events[j][1] / events[i][1])) <= math.log(1.01)
               and (dt.date.fromisoformat(events[j][0]) - dt.date.fromisoformat(events[j - 1][0])).days <= SPLIT_CLUSTER_DAYS):
            j += 1
        cluster = events[i:j]
        if len(cluster) == 1:
            out.append(tuple(cluster[0]))
        else:
            by_day = {}                           # rows landing on the same trading day are one split
            for e in cluster:
                day, move = _split_move(conn, ticker, e[0])
                by_day.setdefault(day or e[0], []).append((e, move))
            cands = [(max(es, key=lambda x: x[0][0])[0], es[0][1], sum(x[0][2] for x in es)) for es in by_day.values()]
            confirmed = [c for c in cands if _fit(c[1], c[0][1]) <= math.log(1.25)]
            n_raw = sum(e[2] for e in cluster)
            if len(confirmed) > 1:                # the prices carry each one: keep them all
                out += [(c[0][0], c[0][1], c[2], "price_confirmed") for c in confirmed]
            else:
                keep = confirmed[0] if confirmed else min(cands, key=lambda c: (_fit(c[1], c[0][1]), -len(c[0][0]), c[0][0]))
                if not confirmed and all(c[1] is None for c in cands):
                    keep = max(cands, key=lambda c: c[0][0])   # no quotes at all: the latest date
                out.append((keep[0][0], keep[0][1], n_raw, "near_date"))
        i = j
    return sorted(out)


def build_split_events(conn=None) -> int:
    """Derive split_events (one row per split) from the raw corporate_actions split rows."""
    conn = conn or db.connect()
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM corporate_actions WHERE action='split'")]
    conn.execute("DELETE FROM split_events")
    n = 0
    for t in tickers:
        rows = [(t, *e) for e in split_events(conn, t)]
        conn.executemany("INSERT INTO split_events (ticker, date, ratio, raw_rows, rule) VALUES (?,?,?,?,?)", rows)
        n += len(rows)
    conn.executemany(
        "INSERT OR REPLACE INTO descriptions "
        "(table_name, indicator, isfilter, isprimarykey, title, description, unittype) "
        "VALUES (?,?,?,?,?,?,?)", SPLIT_EVENTS_DESCRIPTIONS)
    conn.commit()
    return n


SPLIT_EVENTS_DESCRIPTIONS = [
    ("split_events", "ticker", "Y", "Y", "Ticker", "Sharadar ticker (current/last symbol).", "text"),
    ("split_events", "date", "Y", "Y", "Ex-date", "Split ex-date (the date of the corporate_actions row kept).", "text"),
    ("split_events", "ratio", "N", "N", "Split ratio", "s in an s:1 split (0.1 for a 1:10 reverse split).", "ratio"),
    ("split_events", "raw_rows", "N", "N", "Raw rows",
     "Number of corporate_actions split rows this split stands for (more than 1 when the vendor re-served it).", "units"),
    ("split_events", "rule", "N", "N", "Merge rule",
     "'single'; 'same_date' (duplicate values, the most precise kept); 'same_date_conflict' (values differ, the "
     "price move picked one); 'near_date' (same ratio within days, one kept); 'price_confirmed' (same ratio within "
     "days, each confirmed by the price move).", "text"),
]


# ---- PIT shares outstanding ----
# SF1 share counts are restated by the vendor for every split after the filing,
# like SF1 `price`: AAPL's 2014 filings carry ~24.5B shares, about 28x the count
# then outstanding (7:1 in 2014, 4:1 in 2020). Multiplying them by an as-traded
# close overstates market cap by every later split and leaks future splits. The
# count as of day D, in D's as-traded units, is the restated count of the latest
# filing <= D divided by the product of the split ratios with ex-date after D; it
# changes at each filing and at each split ex-date.
#
# The count is period-end basic shares times the ADR share factor (sharesbas x
# sharefactor), the vendor's own marketcap basis: close x that count / later splits
# matches SF1 marketcap within 1% at 99.9% of ARQ report dates, against 71.5% for
# the quarter's weighted average (shareswa), which lags issuance. shareswa is the
# fallback when sharesbas is missing.

def pit_share_events(conn, ticker):
    """Point-in-time share counts for one ticker, oldest first.

    Returns [(from_date, shares, filing_date, restated_shares, split_factor,
    share_field)], one row per SF1 ARQ/ARY filing and per split ex-date after the
    first filing; each count holds from its from_date until the next row's. When
    ARQ and ARY share a filing date, ARQ wins.
    """
    import bisect
    rows = conn.execute(
        "SELECT date, sharesbas, sharefactor, shareswa FROM sf1 WHERE ticker=?"
        " AND dimension IN ('ARQ','ARY') ORDER BY date, dimension DESC", (ticker,)).fetchall()
    filings = []
    for d, bas, factor, wa in rows:
        if bas and bas > 0:
            filings.append((d, bas * (factor if factor and factor > 0 else 1.0), "sharesbas"))
        elif wa and wa > 0:
            filings.append((d, wa, "shareswa"))
    if not filings:
        return []
    splits = conn.execute("SELECT date, ratio FROM split_events WHERE ticker=? ORDER BY date", (ticker,)).fetchall()
    split_dates = [d for d, _ in splits]
    after = [1.0] * (len(splits) + 1)          # after[i] = product of ratios of splits[i:]
    for i in range(len(splits) - 1, -1, -1):
        after[i] = after[i + 1] * splits[i][1]
    latest = {}                                 # filing date -> (restated count, field); ARQ last, so it wins
    for d, n, field in filings:
        latest[d] = (n, field)
    starts = sorted(set(latest) | {d for d in split_dates if d > filings[0][0]})
    events, fi, cur = [], 0, None
    fdates = sorted(latest)
    for d in starts:
        while fi < len(fdates) and fdates[fi] <= d:
            cur = fdates[fi]
            fi += 1
        f = after[bisect.bisect_right(split_dates, d)]   # splits with ex-date after d
        n, field = latest[cur]
        events.append((d, n / f, cur, n, f, field))
    return events


def _shares_on(events, dates, day):
    """Shares outstanding on `day` from pit_share_events (None before the first filing)."""
    import bisect
    i = bisect.bisect_right(dates, day)
    return events[i - 1][1] if i else None


def build_shares_pit(conn=None) -> int:
    """Derive shares_pit (point-in-time shares outstanding) for every stock with SF1 shares.

    Rebuilds split_events first, so the splits it divides by are the current ones.
    """
    conn = conn or db.connect()
    build_split_events(conn)
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM sf1 WHERE dimension IN ('ARQ','ARY')"
        " AND (sharesbas > 0 OR shareswa > 0)")]
    conn.execute("DELETE FROM shares_pit")
    n = 0
    for t in tickers:
        rows = [(t, *e) for e in pit_share_events(conn, t)]
        conn.executemany("INSERT INTO shares_pit (ticker, from_date, shares, filing_date,"
                         " restated_shares, split_factor, share_field) VALUES (?,?,?,?,?,?,?)", rows)
        n += len(rows)
    conn.executemany(
        "INSERT OR REPLACE INTO descriptions "
        "(table_name, indicator, isfilter, isprimarykey, title, description, unittype) "
        "VALUES (?,?,?,?,?,?,?)", SHARES_PIT_DESCRIPTIONS)
    conn.commit()
    return n


SHARES_PIT_DESCRIPTIONS = [
    ("shares_pit", "ticker", "Y", "Y", "Ticker", "Sharadar ticker (current/last symbol).", "text"),
    ("shares_pit", "from_date", "Y", "Y", "From date",
     "First day the count applies: an SF1 ARQ/ARY filing date or a split ex-date. Holds until the next row's from_date.", "text"),
    ("shares_pit", "shares", "N", "N", "Shares outstanding (point-in-time)",
     "Shares outstanding as of from_date in that day's as-traded units: restated_shares / split_factor. "
     "Multiply by the as-traded prices.close for a point-in-time market cap.", "units"),
    ("shares_pit", "filing_date", "N", "N", "Source filing date",
     "Date of the SF1 ARQ/ARY row the count comes from (ARQ wins on a shared date).", "text"),
    ("shares_pit", "restated_shares", "N", "N", "Vendor shares (restated)",
     "That row's SF1 count as served (sharesbas x sharefactor, or shareswa when sharesbas is missing): "
     "restated for every split after the filing.", "units"),
    ("shares_pit", "split_factor", "N", "N", "Later-split factor",
     "Product of the split ratios (s in s:1) with ex-date after from_date; 1.0 when none.", "ratio"),
    ("shares_pit", "share_field", "N", "N", "Source share field",
     "'sharesbas' (period-end basic shares x the ADR sharefactor, the vendor's marketcap basis) or "
     "'shareswa' (weighted average, used only when sharesbas is missing).", "text"),
]


# ---- PIT universe construction ----
_PIT_DEFAULT_TYPES = ("Domestic Common Stock", "Domestic Common Stock Primary Class",
                      "Domestic Common Stock Secondary Class")


def build_universe_pit(conn=None, as_of=None, min_price: float = 2.0,
                       min_mcap: float = 300_000_000.0, min_dvol: float = 5_000_000.0,
                       lookback: int = 20, min_dvol_days: int = 10,
                       max_quote_age: int = 10, types=_PIT_DEFAULT_TYPES) -> int:
    """Construct a point-in-time investable universe from master+prices+sf1.

    As of `as_of`: a stock is investable iff it traded recently enough, its last
    as-traded close >= min_price, trailing avg $volume >= min_dvol over the
    lookback, and PIT market cap (close x point-in-time shares on the close's
    date, see pit_share_events) >= min_mcap. Rows already stored for `as_of`
    are replaced, including names that no longer qualify.
    """
    conn = conn or db.connect()
    build_split_events(conn)                    # the splits the PIT shares divide by
    as_of = as_of or dt.date.today().isoformat()
    types = types or _PIT_DEFAULT_TYPES
    conn.execute("DELETE FROM universe_pit WHERE as_of=?", (as_of,))
    placeholders = ",".join("?" * len(types))
    cands = conn.execute(
        "SELECT ticker, category, exchange, isdelisted, sector, industry,"
        " firstpricedate, lastpricedate FROM securities_master"
        ' WHERE "table"=\'stocks\' AND category IN (' + placeholders + ")",
        list(types)).fetchall()
    n = 0
    LIMIT = lookback + 5
    for ticker, category, exchange, isdelisted, sector, industry, first, last in cands:
        if isdelisted == "Y" and last and last < as_of:
            continue  # delisted before the as-of date
        quotes = conn.execute(
            "SELECT date, close, volume FROM prices WHERE ticker=? AND date<=?"
            " ORDER BY date DESC LIMIT ?", (ticker, as_of, LIMIT)).fetchall()
        if not quotes:
            continue
        last_date, last_close, _ = quotes[0]
        if not last_close or last_close < min_price:
            continue
        age = (dt.date.fromisoformat(as_of) - dt.date.fromisoformat(last_date)).days
        if age > max_quote_age:
            continue
        dvol = [c * v for d, c, v in quotes if c and v]
        dvol_days = len(dvol)
        if dvol_days < min_dvol_days or (sum(dvol) / dvol_days) < min_dvol:
            continue
        events = [e for e in pit_share_events(conn, ticker) if e[2] <= as_of]
        sh = _shares_on(events, [e[0] for e in events], last_date)
        mcap = last_close * sh if sh else None
        if mcap is None or mcap < min_mcap:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO universe_pit "
            "(as_of, ticker, category, exchange, isdelisted, sector, industry,"
            " price, mcap, dvol_avg, dvol_days, firstpricedate, lastpricedate)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (as_of, ticker, category, exchange, isdelisted, sector, industry,
             last_close, mcap, sum(dvol) / dvol_days, dvol_days, first, last))
        n += 1
        conn.commit()
    return n

def build_universe_pit_history(conn, min_price=2.0, min_mcap=300_000_000.0,
                               min_dvol=5_000_000.0, lookback=20,
                               min_dvol_days=10, max_quote_age=10,
                               types=_PIT_DEFAULT_TYPES, start=None, end=None,
                               chunk=400_000):
    """PIT investable universe for EVERY trading day.

    Strategy (fast, unlike per-date loops): for each stock, walk its own price
    rows once, keeping a rolling $volume window (last `lookback` sessions) and
    a pointer into its point-in-time share counts (pit_share_events: the latest
    SF1 ARQ/ARY filing <= row date, divided by the splits after the row date).
    A price row is "valid" when close >= min_price, rolling avg $volume >=
    min_dvol with >= min_dvol_days sessions, and close * PIT shares >= min_mcap.
    Membership on any calendar day D requires the most recent valid price row
    <= D to be within max_quote_age calendar days (fresh quote). Validity runs
    are merged and expanded onto the global trading-day calendar, stored in
    universe_pit(as_of, ticker, ...) -- a row per (day, member). Every stored
    row in the rebuilt window [start, end] is deleted first, so a (day, name)
    that no longer qualifies does not survive a rebuild.

    Returns the number of member-day rows stored.
    """
    import bisect
    conn = conn or db.connect()
    build_split_events(conn)                    # the splits the PIT shares divide by
    types = types or _PIT_DEFAULT_TYPES
    trading = [r[0] for r in conn.execute("SELECT DISTINCT date FROM prices ORDER BY date")]
    if start:
        trading = [d for d in trading if d >= start]
    if end:
        trading = [d for d in trading if d <= end]
    if trading:
        conn.execute("DELETE FROM universe_pit WHERE as_of >= ? AND as_of <= ?",
                     (trading[0], trading[-1]))
    placed = ",".join("?" * len(types))
    cands = conn.execute(
        "SELECT ticker, category, exchange, isdelisted, sector, industry,"
        " firstpricedate, lastpricedate FROM securities_master"
        ' WHERE "table"=\'stocks\' AND category IN (' + placed + ")",
        list(types)).fetchall()
    total = 0
    rows = []
    def flush():
        nonlocal rows
        conn.executemany("INSERT OR REPLACE INTO universe_pit "
                         "(as_of, ticker, category, exchange, isdelisted, sector, industry,"
                         " price, mcap, dvol_avg, dvol_days, firstpricedate, lastpricedate)"
                         " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        rows.clear()
    n_members = 0
    for ticker, category, exchange, isdelisted, sector, industry, first, last in cands:
        if isdelisted == "Y" and last and last < (trading[0] if trading else "9999"):
            continue
        prices = conn.execute(
            "SELECT date, close, volume FROM prices WHERE ticker=? ORDER BY date",
            (ticker,)).fetchall()
        if not prices:
            continue
        shares = pit_share_events(conn, ticker)
        si = 0
        # rolling window over sessions (rows are trading sessions for this ticker)
        wsum, wct, wq = 0.0, 0, []
        valid = []          # (price_row_date, last_close, mcap, dvol_avg, dvol_ct)
        priced = []         # every session with a quote, valid or not
        for d, close, volume in prices:
            if d < (trading[0] if trading else "0000") or close is None:
                continue
            priced.append(d)
            while si < len(shares) and shares[si][0] <= d:
                si += 1
            swa = shares[si - 1][1] if si else None
            vol = volume or 0.0
            wsum += close * vol
            wq.append(close * vol)
            wct += 1 if (volume is not None and volume > 0) else 0
            if len(wq) > lookback:
                drop = wq.pop(0)
                wsum -= drop
                wct -= 1 if drop > 0 else 0
            dv = wsum / wct if wct >= min_dvol_days else 0.0
            if (close >= min_price and dv >= min_dvol
                    and swa is not None and close * swa >= min_mcap):
                valid.append((d, close, close * swa, dv, wct))
        if not valid:
            continue
        # merge validity runs. A valid row's membership runs until the name's next
        # quoted session: through it if that session is valid too, up to the day
        # before it if it fails the screens (a failing quote ends membership; only
        # a gap in quotes is bridged), and at most max_quote_age calendar days.
        # Before 2026-10-25 runs went to the next *valid* row, so failing quotes were
        # bridged on a stale profile (LEHMQ was a $2.5B member at $0.05 in 2008-09).
        valid_days = {v[0] for v in valid}
        runs = []   # each: [run_start, run_end, [valid tuples inside the run]]
        for i, (d, close, mcap, dv, dct) in enumerate(valid):
            k = bisect.bisect_right(priced, d)
            if k == len(priced):                       # no later quote: bridge the gap
                nxt = last or trading[-1]
            elif priced[k] in valid_days:
                nxt = priced[k]
            else:                                      # next quote fails: stop the day before it
                nxt = trading[bisect.bisect_left(trading, priced[k]) - 1]
            end = min(nxt, _add_days(d, max_quote_age))  # calendar-day cap
            if runs and runs[-1][1] >= d:
                runs[-1][1] = max(runs[-1][1], end)
                runs[-1][2].append((d, close, mcap, dv, dct))
            else:
                runs.append([d, end, [(d, close, mcap, dv, dct)]])
        # expand runs onto the trading calendar. The stored profile (price,
        # mcap, dvol) is the MOST RECENT valid quote as of each member day --
        # NOT the run-start quote (a continuous 1998->now run must not carry
        # its 1998 price forward; bug fixed 2026-08-12).
        for d0, d1, rvalid in runs:
            i0 = bisect.bisect_left(trading, d0)
            i1 = bisect.bisect_right(trading, d1)
            n_members += max(0, i1 - i0)
            j = 0
            for idx in range(i0, i1):
                day = trading[idx]
                while j + 1 < len(rvalid) and rvalid[j + 1][0] <= day:
                    j += 1
                _, close, mcap, dv, dct = rvalid[j]
                rows.append((day, ticker, category, exchange, isdelisted,
                             sector, industry, close, mcap, dv, dct, first, last))
                if len(rows) >= chunk:
                    flush()
        total += 1
    flush()
    return n_members


def _add_days(dstr, n):
    from datetime import date as D, timedelta
    y, m, d = map(int, dstr.split("-"))
    return (D(y, m, d) + timedelta(days=n)).isoformat()
