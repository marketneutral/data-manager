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
                    conn=None) -> list[dict]:
    """Return as-of OHLCV with query-time adjustments applied.

    adjustment is the per-day factor (yf Adj Close / Close: splits + dividends).
    Adjusted field = raw field * adjustment, so open/high/low/close can all be
    adjusted consistently. Volume is left as traded. Returns dicts keyed
    date/open/high/low/close/adjusted_open/.../adjustment.
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
    out = []
    for r in rows:
        adj = r["adjustment"] if r["adjustment"] is not None else 1.0
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
_SF1_TYPED = ["revenue", "netinc", "netinccmn", "assets", "liabilities", "equity",
              "cashneq", "ncfo", "capex", "fcf", "marketcap", "ev", "pe", "pb",
              "ps", "price", "eps", "dps", "divyield", "roe", "roa", "roic",
              "grossmargin", "netmargin", "ebitda", "shareswa", "shareswadil",
              "currentratio", "de"]

# rough rows-per-ticker estimates per dimension (from live probes)
_EST = {"ARY": 38, "MRY": 40, "ARQ": 140, "MRQ": 145}


def _sf1_batches(tickers: list[str], dimension: str) -> list[list[str]]:
    """Batches for the fundamentals endpoint: server caps ~30 tickers/request."""
    n = 25
    return [tickers[i:i + n] for i in range(0, len(tickers), n)]


def update_sf1_all(conn=None, dimensions=None, pace: float = 0.15) -> int:
    """Full SF1 mirror (all dimensions, full history) via batched pulls."""
    from .providers.sharadar import _fetch
    import json, zlib
    conn = conn or db.connect()
    dims = dimensions or _SF1_DIMS
    total = 0
    tickers = master_stocks(conn)
    for dim in dims:
        for batch in _sf1_batches(tickers, dim):
            rows = _fetch("fundamentals", ticker=",".join(batch), dimension=dim)
            if not rows:
                continue
            for r in rows:
                blob = zlib.compress(json.dumps(r).encode("utf-8"))
                conn.execute(
                    "INSERT OR REPLACE INTO sf1 "
                    "(ticker, dimension, date, reportperiod, fiscalperiod, calendardate,"
                    " lastupdated, revenue, netinc, netinccmn, assets, liabilities,"
                    " equity, cashneq, ncfo, capex, fcf, marketcap, ev, pe, pb, ps,"
                    " price, eps, dps, divyield, roe, roa, roic, grossmargin,"
                    " netmargin, ebitda, shareswa, shareswadil, currentratio, de, data)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                    "?,?,?,?,?,?,?,?,?,?)",
                    (r.get("ticker"), dim, r.get("date"), r.get("reportperiod"),
                     r.get("fiscalperiod"), r.get("calendardate"), r.get("lastupdated"),
                     *[_numf(r.get(c)) for c in _SF1_TYPED], blob))
            total += len(rows)
            conn.commit()
            time.sleep(pace)
    return total


# ---- prices for ALL stocks, size-aware batched (ticker=A,B,... supported) ----
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
def _hydrate_sf1(conn, ticker: str, dimension: str) -> list[dict]:
    import json, zlib
    rows = conn.execute(
        "SELECT data FROM sf1 WHERE ticker=? AND dimension=? ORDER BY calendardate",
        (ticker, dimension)).fetchall()
    out = []
    for (blob,) in rows:
        if blob:
            out.append(json.loads(zlib.decompress(blob).decode("utf-8")))
    return out


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
                " d_liquidity, equity_issuance, d_gross_margin, d_asset_turnover, f_score)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (t, f["fiscal_year"], f.get("roa"), f.get("cfo"), f.get("d_roa"),
                 f.get("accruals"), f.get("d_leverage"), f.get("d_liquidity"),
                 f.get("equity_issuance"), f.get("d_gross_margin"),
                 f.get("d_asset_turnover"), f.get("f_score")))
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
                " shares_out, roa, cfo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (t, r["period"], r["net_income"], r["revenue"], r["gross_profit"],
                 r["operating_cash_flow"], r["total_assets"], r["total_liabilities"],
                 r["current_assets"], r["current_liabilities"], r["shares_out"],
                 r["roa"], r["cfo"]))
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
    lookback, and PIT market cap (close x latest as-reported shares) >= min_mcap.
    """
    conn = conn or db.connect()
    as_of = as_of or dt.date.today().isoformat()
    types = types or _PIT_DEFAULT_TYPES
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
        sh = conn.execute(
            "SELECT shareswa FROM sf1 WHERE ticker=? AND dimension IN ('ARQ','ARY')"
            " AND date<=? ORDER BY date DESC LIMIT 1", (ticker, as_of)).fetchone()
        mcap = last_close * sh[0] if (sh and sh[0]) else None
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
    a pointer to the latest as-reported shares (SF1 ARQ/ARY <= row date).
    A price row is "valid" when close >= min_price, rolling avg $volume >=
    min_dvol with >= min_dvol_days sessions, and close*shareswa >= min_mcap.
    Membership on any calendar day D requires the most recent valid price row
    <= D to be within max_quote_age calendar days (fresh quote). Validity runs
    are merged and expanded onto the global trading-day calendar, stored in
    universe_pit(as_of, ticker, ...) -- a row per (day, member).

    Returns the number of member-day rows stored.
    """
    import bisect
    conn = conn or db.connect()
    types = types or _PIT_DEFAULT_TYPES
    trading = [r[0] for r in conn.execute("SELECT DISTINCT date FROM prices ORDER BY date")]
    if start:
        trading = [d for d in trading if d >= start]
    if end:
        trading = [d for d in trading if d <= end]
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
        shares = conn.execute(
            "SELECT date, shareswa FROM sf1 WHERE ticker=? AND dimension IN ('ARQ','ARY')"
            " AND shareswa IS NOT NULL ORDER BY date", (ticker,)).fetchall()
        si = 0
        # rolling window over sessions (rows are trading sessions for this ticker)
        wsum, wct, wq = 0.0, 0, []
        valid = []          # (price_row_date, last_close, mcap, dvol_avg, dvol_ct)
        for d, close, volume in prices:
            if d < (trading[0] if trading else "0000") or close is None:
                continue
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
        # merge validity runs: membership days for row i = [d_i, min(d_{i+1}-1, d_i+max_quote_age)]
        runs = []
        for i, (d, close, mcap, dv, dct) in enumerate(valid):
            nxt = valid[i + 1][0] if i + 1 < len(valid) else (last or trading[-1])
            end = min(nxt, _add_days(d, max_quote_age))  # calendar-day cap
            if runs and runs[-1][1] >= d:
                runs[-1][1] = max(runs[-1][1], end)
            else:
                runs.append([d, end, close, mcap, dv, dct])
        # expand runs onto the trading calendar
        for d0, d1, close, mcap, dv, dct in runs:
            i0 = bisect.bisect_left(trading, d0)
            i1 = bisect.bisect_right(trading, d1)
            n_members += max(0, i1 - i0)
            for idx in range(i0, i1):
                rows.append((trading[idx], ticker, category, exchange, isdelisted,
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
