"""Point-in-time accessors: what was known on a decision date, and nothing after.

Each function answers "as of date D" so consumers do not write their own as-of joins (an
exact-date join silently drops facts whose knowledge date is a weekend or holiday). D can be
any calendar date; a fact applies from its knowledge date onward.

    from data_manager import pit
    pit.pit_members("2014-06-06")                            # {ticker: profile}
    pit.pit_market_cap("2014-06-06", ["AAPL", "MSFT"])      # {ticker: $}
    pit.pit_fundamentals("2014-06-06", ["revenue", "eps"])  # {ticker: {field: value, ...}}

See AGENTS.md, "Point-in-time contract", for what each table's dates mean.
"""
from __future__ import annotations

from . import db

# SF1 fields served in the latest share units (restated for every later split); see AGENTS.md
RESTATED_PER_SHARE = ("price", "eps", "epsdil", "epsusd", "bvps", "dps", "sps", "fcfps", "tbvps")
RESTATED_SHARES = ("sharesbas", "shareswa", "shareswadil")
AS_REPORTED = ("ARQ", "ARY", "ART")
RESTATED_DIMENSIONS = ("MRQ", "MRY", "MRT")


def _placeholders(xs):
    return ",".join("?" * len(xs))


def split_factor_after(date: str, tickers=None, conn=None) -> dict:
    """{ticker: product of the split ratios with ex-date after `date`} (1.0 when none)."""
    conn = conn or db.connect()
    sql = "SELECT ticker, CAST(value AS REAL) FROM corporate_actions WHERE action='split' AND date > ? AND CAST(value AS REAL) > 0"
    params = [date]
    if tickers:
        sql += f" AND ticker IN ({_placeholders(tickers)})"
        params += list(tickers)
    out = {}
    for t, r in conn.execute(sql, params):
        out[t] = out.get(t, 1.0) * r
    if tickers:
        for t in tickers:
            out.setdefault(t, 1.0)
    return out


def pit_fundamentals(date: str, fields, dimension: str = "ARQ", tickers=None, conn=None,
                     split_basis: bool = True, allow_restated: bool = False) -> dict:
    """{ticker: {field: value, 'filing_date', 'reportperiod'}} from each name's latest SF1 filing known on `date`.

    As-reported dimensions only (ARQ/ARY/ART: `date` is the SEC filing date, and the values are the
    original filing's). MR* rows are restated after the fact and are refused unless allow_restated.
    With split_basis (default), per-share fields are multiplied, and share counts divided, by the
    splits after `date`, so their levels are in `date`'s share units instead of today's.
    """
    if dimension in RESTATED_DIMENSIONS and not allow_restated:
        raise ValueError(f"{dimension} is restated after the fact (look-ahead); use {AS_REPORTED} or allow_restated=True")
    if dimension not in AS_REPORTED + RESTATED_DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}")
    conn = conn or db.connect()
    fields = list(fields)
    known = {r[1] for r in conn.execute("PRAGMA table_info(sf1)")}
    bad = [f for f in fields if f not in known]
    if bad:
        raise ValueError(f"not SF1 columns: {bad}")
    cols = ", ".join(f'"{f}"' for f in fields)
    sql = (f"SELECT ticker, date, reportperiod, {cols} FROM ("
           f" SELECT ticker, date, reportperiod, {cols},"
           f"        ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC, reportperiod DESC) AS rn"
           f" FROM sf1 WHERE dimension = ? AND date <= ?"
           + (f" AND ticker IN ({_placeholders(tickers)})" if tickers else "") +
           ") WHERE rn = 1")
    params = [dimension, date] + (list(tickers) if tickers else [])
    out = {}
    for row in conn.execute(sql, params):
        t, filed, rp, vals = row[0], row[1], row[2], row[3:]
        out[t] = {"filing_date": filed, "reportperiod": rp, **dict(zip(fields, vals))}
    if split_basis and any(f in RESTATED_PER_SHARE + RESTATED_SHARES for f in fields):
        f_after = split_factor_after(date, list(out), conn=conn)
        for t, rec in out.items():
            k = f_after.get(t, 1.0)
            for f in fields:
                if rec[f] is None:
                    continue
                if f in RESTATED_PER_SHARE:
                    rec[f] = rec[f] * k
                elif f in RESTATED_SHARES:
                    rec[f] = rec[f] / k
    return out


def pit_shares(date: str, tickers=None, conn=None) -> dict:
    """{ticker: shares outstanding on `date` in that day's as-traded units} (shares_pit, latest from_date <= date)."""
    conn = conn or db.connect()
    sql = ("SELECT ticker, shares FROM (SELECT ticker, shares, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY from_date DESC) rn"
           " FROM shares_pit WHERE from_date <= ?" + (f" AND ticker IN ({_placeholders(tickers)})" if tickers else "") +
           ") WHERE rn = 1")
    return dict(conn.execute(sql, [date] + (list(tickers) if tickers else [])).fetchall())


def pit_market_cap(date: str, tickers=None, conn=None, max_quote_age: int = 10) -> dict:
    """{ticker: as-traded close x PIT shares} using each name's latest quote within max_quote_age days of `date`.

    Covers every stock with a quote and SF1 shares, member or not (universe_pit carries mcap for members).
    """
    conn = conn or db.connect()
    sql = ("SELECT ticker, close, date FROM (SELECT ticker, close, date, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn"
           " FROM prices WHERE date <= ? AND date >= date(?, ?) AND close > 0"
           + (f" AND ticker IN ({_placeholders(tickers)})" if tickers else "") + ") WHERE rn = 1")
    quotes = conn.execute(sql, [date, date, f"-{int(max_quote_age)} days"] + (list(tickers) if tickers else [])).fetchall()
    out = {}
    for t, close, qdate in quotes:
        sh = pit_shares(qdate, [t], conn=conn).get(t)        # shares in the quote day's units
        if sh:
            out[t] = close * sh
    return out


def pit_members(date: str, conn=None) -> dict:
    """{ticker: {price, mcap, dvol_avg, sector, industry}} for the investable universe on `date`.

    universe_pit has a row per trading day; for a non-trading `date` the latest trading day before it is used.
    sector and industry are the latest classification (no history), not as of `date`.
    """
    conn = conn or db.connect()
    d = conn.execute("SELECT MAX(as_of) FROM universe_pit WHERE as_of <= ?", (date,)).fetchone()[0]
    if d is None:
        return {}
    return {t: {"as_of": d, "price": p, "mcap": m, "dvol_avg": v, "sector": s, "industry": i}
            for t, p, m, v, s, i in conn.execute(
                "SELECT ticker, price, mcap, dvol_avg, sector, industry FROM universe_pit WHERE as_of = ?", (d,))}
