"""Reading the store: the state as of any load, adjusted prices as of any date, shares outstanding on any day.

Adjusted prices are computed here, from the as-traded prices and the events. For a price row on day t the event
factor is E(t) = q(t) / s(t): s(t) the split ratios with ex-date in (previous row, t], q(t) the cash dividends'
(1 - dividend / previous close) times any other adjustment there. The price adjusted as of day D is

    close_traded(t) x prod of E(u) for rows t < u <= D

which uses only events known by D, and is 1 x close_traded on D itself. Its returns are total returns.
"""
import json

import numpy as np
import pandas as pd

from .keys import DAY, Keys
from .schema import TABLES, connect


def rows(conn, table, at=None, where="", params=(), columns=None):
    """A table's rows as of load `at` (None: the current rows): those added by then and not yet superseded."""
    key, vals = TABLES[table]
    cols = ", ".join(columns or (key + vals + ["source", "added_in"]))
    if at is None:
        cond, args = "superseded_in IS NULL", []
    else:
        cond, args = "added_in <= ? AND (superseded_in IS NULL OR superseded_in > ?)", [at, at]
    sql = f"SELECT {cols} FROM {table} WHERE {cond}" + (f" AND ({where})" if where else "")
    return pd.read_sql(sql, conn, params=args + list(params))


def loads(conn):
    out = pd.read_sql("SELECT * FROM loads ORDER BY load_id", conn)
    out["summary"] = out.summary.map(lambda s: json.loads(s) if s else None)
    return out


def last_accepted(conn):
    r = conn.execute("SELECT max(load_id) FROM loads WHERE status='accepted'").fetchone()
    return r[0]


def event_factors(prices, splits, dividends, other):
    """log E(t) for each price row (prices sorted by security and date), from the events."""
    px = prices.reset_index(drop=True)
    evs = [e for e in (splits, dividends, other) if e is not None and len(e)]
    K = Keys(px.security_id, *[e.security_id for e in evs])
    pkey = K.key(px.security_id, px.date)
    if len(pkey) > 1 and not (pkey[:-1] <= pkey[1:]).all():
        raise ValueError("prices must be sorted by security_id, date")
    code = pkey // DAY
    same = np.concatenate([[False], code[1:] == code[:-1]])
    cprev = px.close_traded.shift().to_numpy()
    logE = np.zeros(len(px))

    def place(ev, date_col="ex_date"):
        ek = K.key(ev.security_id, ev[date_col])
        pos = np.searchsorted(pkey, ek)
        ok = pos < len(pkey)
        ok[ok] = code[pos[ok]] == ek[ok] // DAY
        ok &= same[np.minimum(pos, len(pkey) - 1)]          # an event before a security's first row changes nothing
        return pos, ok

    if splits is not None and len(splits):
        pos, ok = place(splits)
        np.add.at(logE, pos[ok], -np.log(splits.ratio.to_numpy(dtype=float)[ok]))
    if dividends is not None and len(dividends):
        pos, ok = place(dividends)
        amount = np.zeros(len(px))
        np.add.at(amount, pos[ok], dividends.amount_traded.to_numpy(dtype=float)[ok])
        with np.errstate(divide="ignore", invalid="ignore"):
            q = np.where(amount > 0, 1 - amount / cprev, 1.0)
        logE += np.log(np.where(q > 0, q, 1.0))
    if other is not None and len(other):
        pos, ok = place(other)
        np.add.at(logE, pos[ok], np.log(other.factor.to_numpy(dtype=float)[ok]))
    return logE


def adjusted(prices, splits, dividends, other, as_of=None, fields=("open", "high", "low", "close")):
    """Total-return adjusted prices as of `as_of` (a date; None: each security's last row), one column per field.
    Rows after as_of are dropped. Volume adjusted the same way divides by the factor."""
    if as_of is not None:
        prices = prices[prices.date <= as_of]
    K = Keys(prices.security_id)
    pk = K.key(prices.security_id, prices.date)
    order = np.argsort(pk, kind="stable")
    px = prices.iloc[order].reset_index(drop=True)
    code = pk[order] // DAY
    logE = event_factors(px, splits, dividends, other)
    S = pd.Series(logE).groupby(code).cumsum().to_numpy()
    last = pd.Series(S).groupby(code).transform("last").to_numpy()
    factor = np.exp(last - S)
    out = px[["security_id", "date"]].copy()
    for f in fields:
        out[f] = px[f"{f}_traded"] * factor
    if "volume_traded" in px:
        out["volume"] = px.volume_traded / factor
    out["factor"] = factor
    return out


def shares_on(shares, splits, security_id, dates):
    """Shares outstanding on each date, in that day's units: the latest filing on or before it (ARQ before ARY on
    the same date) times the splits with ex-date after the filing and on or before the date."""
    s = shares[shares.security_id == security_id].sort_values(["filing_date", "dimension"], ascending=[True, False])
    s = s.drop_duplicates("filing_date", keep="first")
    sp = splits[splits.security_id == security_id].sort_values("ex_date")
    dates = pd.Index(dates)
    k = np.searchsorted(s.filing_date.to_numpy(), dates.to_numpy(), side="right") - 1
    out = np.full(len(dates), np.nan)
    has = k >= 0
    out[has] = s.shares_filed.to_numpy()[k[has]]
    for d, r in zip(sp.ex_date, sp.ratio):
        filed = np.where(has, s.filing_date.to_numpy()[np.maximum(k, 0)], "")
        out *= np.where(has & (filed < d) & (dates.to_numpy() >= d), r, 1.0)
    return pd.Series(out, index=dates)


__all__ = ["connect", "rows", "loads", "last_accepted", "adjusted", "event_factors", "shares_on"]
