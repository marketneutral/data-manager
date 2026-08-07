"""Orchestration: fetch data from providers and store into SQLite."""

import datetime as dt

from . import db
from .providers.financialdatasets import FinancialDatasetsProvider
from .providers.yfinance import YFinanceProvider


def update_universe(conn=None, provider=None) -> int:
    """Fetch R3000 constituents and store them in the universe table.

    Returns the number of tickers stored.
    """
    conn = conn or db.connect()
    provider = provider or FinancialDatasetsProvider()
    constituents = provider.get_universe()
    now = dt.datetime.utcnow().isoformat()
    conn.executemany(
        "INSERT OR REPLACE INTO universe (ticker, name, source, added_at) "
        "VALUES (?, ?, ?, ?)",
        [(c["ticker"], c.get("name"), c.get("source"), now) for c in constituents],
    )
    conn.commit()
    return len(constituents)


def update_prices(tickers, start, end, conn=None, provider=None) -> int:
    """Fetch daily OHLCV prices for the given tickers and store them.

    Returns the number of price rows stored.
    """
    conn = conn or db.connect()
    provider = provider or YFinanceProvider()
    total = 0
    for ticker in tickers:
        rows = provider.get_prices(ticker, start, end)
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO prices "
                "(ticker, date, open, high, low, close, adj_close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(ticker, r["date"], r["open"], r["high"], r["low"],
                  r["close"], r["adj_close"], r["volume"]) for r in rows],
            )
            total += len(rows)
    conn.commit()
    return total


def update_classifications(tickers, conn=None, provider=None) -> int:
    """Fetch sector/industry for the given tickers and store them."""
    conn = conn or db.connect()
    provider = provider or YFinanceProvider()
    count = 0
    for ticker in tickers:
        c = provider.get_classification(ticker)
        conn.execute(
            "INSERT OR REPLACE INTO classifications (ticker, sector, industry, as_of) "
            "VALUES (?, ?, ?, ?)",
            (ticker, c.get("sector"), c.get("industry"), dt.date.today().isoformat()),
        )
        count += 1
    conn.commit()
    return count


def update_fundamentals(tickers, conn=None, provider=None) -> int:
    """Fetch annual fundamentals (Piotroski F-Score) for the given tickers."""
    conn = conn or db.connect()
    provider = provider or YFinanceProvider()
    total = 0
    for ticker in tickers:
        rows = provider.get_fundamentals(ticker)
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
    conn.commit()
    return total


def universe_tickers(conn=None) -> list[str]:
    """Return the list of tickers currently in the universe table."""
    conn = conn or db.connect()
    rows = conn.execute("SELECT ticker FROM universe ORDER BY ticker").fetchall()
    return [r["ticker"] for r in rows]
