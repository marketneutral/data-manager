"""Orchestration: fetch data from providers and store into SQLite."""

import datetime as dt
import time

from . import db
from .providers.ishares import ISharesProvider
from .providers.financialdatasets import FinancialDatasetsProvider
from .providers.yfinance import YFinanceProvider
from .providers.fmp import FMPProvider, _key as _fmp_key


def _default_data_provider():
    """FMP when an API key is present (cleaner fundamentals/ratios), else yfinance."""
    return FMPProvider() if _fmp_key() else YFinanceProvider()


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
    conn.executemany(
        "INSERT OR REPLACE INTO universe (ticker, name, source, added_at) "
        "VALUES (?, ?, ?, ?)",
        [(c["ticker"], c.get("name"), c.get("source"), now) for c in constituents],
    )
    # If the provider carries sector info (e.g. iShares), store classifications too.
    if any(c.get("sector") for c in constituents):
        today = dt.date.today().isoformat()
        for c in constituents:
            if c.get("sector"):
                conn.execute(
                    "INSERT OR REPLACE INTO classifications "
                    "(ticker, sector, industry, as_of) VALUES (?, ?, NULL, ?)",
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


def update_prices(tickers, start, end, conn=None, provider=None, pace: float = 0.25) -> int:
    """Fetch daily OHLCV prices for the given tickers and store them.

    Resumable: tickers whose stored data already covers `end` are skipped.
    Resilient: per-ticker errors are logged and skipped (rate limits, delisted).
    Pacer: `pace` seconds between tickers to stay under yfinance limits.
    Returns the number of price rows stored.
    """
    conn = conn or db.connect()
    provider = provider or _default_data_provider()
    total = 0
    for ticker in tickers:
        row = conn.execute("SELECT MAX(date) FROM prices WHERE ticker=?", (ticker,)).fetchone()
        if row and row[0] and row[0] >= end:
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


def update_classifications(tickers, conn=None, provider=None) -> int:
    """Fetch sector/industry for the given tickers and store them."""
    conn = conn or db.connect()
    provider = provider or _default_data_provider()
    count = 0
    for ticker in tickers:
        have = conn.execute(
            "SELECT industry FROM classifications WHERE ticker=?", (ticker,)).fetchone()
        if have and have[0]:
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


def update_fundamentals(tickers, conn=None, provider=None, pace: float = 0.5) -> int:
    """Fetch annual fundamentals (Piotroski F-Score) for the given tickers.

    Resumable: tickers already having any fundamentals rows are skipped.
    Resilient: per-ticker errors are logged and skipped.
    Returns number of fundamental rows stored.
    """
    conn = conn or db.connect()
    provider = provider or _default_data_provider()
    total = 0
    for ticker in tickers:
        row = conn.execute("SELECT COUNT(*) FROM fundamentals WHERE ticker=?", (ticker,)).fetchone()
        if row and row[0]:
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


def update_ratios(tickers, conn=None, provider=None, pace: float = 0.5) -> int:
    """Snapshot point-in-time fundamental ratios per ticker (yfinance info).

    Resumable: skips tickers already snapshotted today. Resilient + paced like
    the other yfinance jobs. Returns count of ratio rows stored.
    """
    conn = conn or db.connect()
    provider = provider or _default_data_provider()
    as_of = dt.date.today().isoformat()
    total = 0
    for ticker in tickers:
        have = conn.execute(
            "SELECT COUNT(*) FROM ratios WHERE ticker=? AND as_of=?", (ticker, as_of)).fetchone()
        if have and have[0]:
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


def update_quarterly(tickers, conn=None, provider=None, pace: float = 0.2) -> int:
    """Fetch ~10y of quarterly statements (FMP). Resumable: skips tickers with rows."""
    conn = conn or db.connect()
    provider = provider or _default_data_provider()
    total = 0
    for ticker in tickers:
        have = conn.execute("SELECT COUNT(*) FROM quarterly_statements WHERE ticker=?", (ticker,)).fetchone()[0]
        if have:
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
