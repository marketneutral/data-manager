"""The panel: dates x stocks parquet files and a manifest, built from the store for research and the arena.

    data-manager export-panel --out ~/data/panels           # a new versioned folder from the store's current state

Each build writes a new folder named by its version (a hash of its contents) and never overwrites one. The manifest
(panel.json) records the store load it was built from, the date range, the builder's version, a checksum per file,
and a declaration for every field: what it is, its unit, its adjustment exponent and when it is known. The
adjustment exponent says how a field carries the total-return factor A(t), which folds in every split and dividend
after day t: an adjusted price is the price as traded x A(t) (exponent +1), adjusted volume is volume as traded / A(t)
(exponent -1), and anything as traded or as filed is exponent 0. qex reads the declarations to tell which
expressions use adjusted levels in a way that looks ahead.

Who is in the panel: every security the store has as a stock whose category is a domestic common stock, listed or
delisted (no survivorship). Columns are tickers (unique in the vendor's current file); panel.json maps each to its
security_id. Rows: every date on which some candidate traded.
"""
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from .store import read as R
from .store.schema import connect

CATEGORIES = ("Domestic Common Stock", "Domestic Common Stock Primary Class", "Domestic Common Stock Secondary Class")
MARKET = "SPY"
LABELS = ("sector", "industry", "category", "exchange", "famaindustry")
CONTRACT = 1

FIELDS = {
    "open":   dict(unit="usd_per_share", adjustment=1, known="close", about="the open, total-return adjusted as of the panel's last day; for returns"),
    "high":   dict(unit="usd_per_share", adjustment=1, known="close", about="the high, total-return adjusted"),
    "low":    dict(unit="usd_per_share", adjustment=1, known="close", about="the low, total-return adjusted"),
    "close":  dict(unit="usd_per_share", adjustment=1, known="close", about="the close, total-return adjusted; for returns"),
    "volume": dict(unit="shares", adjustment=-1, known="close", about="shares traded / the adjustment factor: smooth across splits; close x volume is dollars traded"),
    "price":  dict(unit="usd_per_share", adjustment=0, known="close", about="the close as printed that day, on every quoted session; for $ levels and market cap"),
    "volume_traded": dict(unit="shares", adjustment=0, known="close", about="shares traded as printed that day; 0 on a quoted session with no trades"),
    "shares": dict(unit="shares", adjustment=0, known="filing", about="shares outstanding in that day's units: the latest filing (from its filing date) times the splits since"),
    "market": dict(unit="usd_per_share", adjustment=1, known="close", about=f"{MARKET}'s close, total-return adjusted (one column)"),
}
for g in LABELS:
    FIELDS[g] = dict(unit="label", adjustment=None, known="current", about=f"the vendor's current {g}: the same on every day (no history yet)")


def _git_version():
    try:
        here = Path(__file__).resolve().parent
        return subprocess.run(["git", "-C", str(here), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except Exception:
        return None


def _wide(df, col, dates, tickers):
    w = df.pivot(index="date", columns="ticker", values=col)
    w.index = pd.DatetimeIndex(pd.to_datetime(w.index), name="date")
    return w.reindex(index=dates, columns=tickers).astype("float64")


def build(out, store=None, at=None, end=None):
    """Build a panel from the store (as of load `at`, default the last accepted) into a new folder under `out`."""
    conn = connect(store, readonly=True)
    at = at or R.last_accepted(conn)
    if at is None:
        raise ValueError("the store has no accepted load")
    sec = R.rows(conn, "securities", at=at)
    stocks = sec[(sec.asset_class == "stock") & sec.category.isin(CATEGORIES)].sort_values("ticker")
    ids = stocks.security_id.tolist()
    mk = sec[(sec.ticker == MARKET) & (sec.asset_class == "fund")].security_id.tolist()

    def fetch(table):
        parts = []
        for i in range(0, len(ids + mk), 900):
            p = (ids + mk)[i:i + 900]
            parts.append(R.rows(conn, table, at=at, where=f"security_id IN ({','.join('?' * len(p))})", params=p))
        return pd.concat(parts, ignore_index=True)

    px, sp, dv, ot, sh = (fetch(t) for t in ("prices", "splits", "dividends", "other_adjustments", "shares"))
    if end:
        px = px[px.date <= end]
    px = px.sort_values(["security_id", "date"]).reset_index(drop=True)
    adj = R.adjusted(px, sp, dv, ot)                       # as of each security's last row: its factor ends at 1
    px["factor"] = adj.factor.to_numpy()
    tick = dict(zip(sec.security_id, sec.ticker))
    px["ticker"] = px.security_id.map(tick)
    quoted = px.close_traded > 0
    traded = quoted & (px.volume_traded > 0)
    stock_px = px[px.security_id.isin(ids)]
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(stock_px.date[traded[stock_px.index]].unique())), name="date")
    tickers = pd.Index(stocks.ticker, name="ticker")

    folder = Path(out).expanduser() / "_building"
    if folder.exists():
        import shutil
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    t = stock_px[traded[stock_px.index]].copy()
    for f in ("open", "high", "low", "close"):
        t[f] = t[f"{f}_traded"] * t.factor
        _wide(t, f, dates, tickers).to_parquet(folder / f"{f}.parquet")
    t["volume"] = t.volume_traded / t.factor
    _wide(t, "volume", dates, tickers).to_parquet(folder / "volume.parquet")
    q = stock_px[quoted[stock_px.index]]
    _wide(q, "close_traded", dates, tickers).to_parquet(folder / "price.parquet")
    _wide(q.assign(v=q.volume_traded.fillna(0)), "v", dates, tickers).to_parquet(folder / "volume_traded.parquet")

    # shares outstanding on every date, per security: the latest filing times the splits since it
    day_str = dates.strftime("%Y-%m-%d")
    shares = np.full((len(dates), len(tickers)), np.nan)
    col = {tk: j for j, tk in enumerate(tickers)}
    sh_by = dict(tuple(sh.groupby("security_id")))
    sp_by = dict(tuple(sp.groupby("security_id")))
    empty_sp = sp.iloc[:0]
    for sid, tk in zip(stocks.security_id, stocks.ticker):
        if sid in sh_by:
            shares[:, col[tk]] = R.shares_on(sh_by[sid], sp_by.get(sid, empty_sp), sid, day_str).to_numpy()
    pd.DataFrame(shares, index=dates, columns=tickers).to_parquet(folder / "shares.parquet")

    m = px[px.security_id.isin(mk) & traded]
    market = pd.Series((m.close_traded * m.factor).to_numpy(), index=pd.to_datetime(m.date), name=MARKET).reindex(dates)
    market.to_frame().to_parquet(folder / "market.parquet")
    labels = stocks.set_index("ticker")[list(LABELS)]
    labels.index.name = "ticker"
    labels.to_parquet(folder / "labels.parquet")

    files = {}
    h = hashlib.sha256()
    for p in sorted(folder.glob("*.parquet")):
        d = hashlib.sha256(p.read_bytes()).hexdigest()
        files[p.name] = dict(sha256=d, size=p.stat().st_size)
        h.update(f"{p.name}:{d}".encode())
    version = h.hexdigest()[:16]
    load = conn.execute("SELECT load_id, data_through, files FROM loads WHERE load_id=?", (at,)).fetchone()
    manifest = dict(
        version=version, contract=CONTRACT, built_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        builder=dict(package="data-manager", commit=_git_version()),
        store=dict(load_id=load[0], data_through=load[1], vendor_files=json.loads(load[2]) if load[2] else None),
        sources={f: "sharadar" for f in FIELDS},
        start=str(dates[0].date()) if len(dates) else None, end=str(dates[-1].date()) if len(dates) else None,
        n_dates=len(dates), n_tickers=len(tickers), market=MARKET,
        fields=FIELDS, files=files,
        tickers={tk: sid for tk, sid in zip(stocks.ticker, stocks.security_id)},
        rules=dict(candidates=f"asset_class stock, category in {list(CATEGORIES)}, listed or delisted",
                   adjusted_fields="on traded sessions (volume_traded > 0)",
                   price_and_volume_traded="on every quoted session (close > 0)",
                   adjustment=f"total-return, as of each security's last row, from the store's events"),
    )
    (folder / "panel.json").write_text(json.dumps(manifest, indent=1, default=str))
    final = Path(out).expanduser() / version
    if final.exists():
        import shutil
        shutil.rmtree(folder)
        return final, manifest
    folder.rename(final)
    return final, manifest
