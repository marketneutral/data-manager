"""The Sharadar adapter: the vendor's rows -> the store's facts. The only module that names Sharadar's columns.

What the vendor serves (verified against its bulk file, 2026-11-06):
  SEP/SFP open, high, low, close   split-adjusted, rounded to 3 decimals
  SEP/SFP closeunadj               the close as printed (3 decimals)
  SEP/SFP closeadj                 adjusted for splits, stock dividends, cash dividends and spinoffs (3 decimals)
  SEP/SFP volume                   split-adjusted (fractions of a share appear), not as traded
  ACTIONS dividend value           per share, in the latest share units (divided by every later split)
  SF1 sharesbas, shareswa          restated for every split after the filing

The splits the vendor applied show in its own split factor on each row, f = closeunadj / close, which steps by the
ratio on the ex-date. Splits are taken from those steps (the exact ratio from the ACTIONS split row when one is near),
so they are the ones the vendor's split-adjusted numbers carry. As traded, then:

    close_traded  = closeunadj
    open_traded   = open x F         F = the product of the split ratios with ex-date after the row's date
    volume_traded = volume / F
    dividend      = value x F(ex-date)
    shares_filed  = (sharesbas x sharefactor, else shareswa) / F(filing date)

The vendor's total-return step on day t is q = closeadj(t-1) close(t) / (closeadj(t) close(t-1)): 1 - dividend /
closeunadj(t-1) on a dividend's ex-date (97.6% within 0.1%), 1 on most days. A step it took that a split or dividend
does not explain, near a spinoff (or any step over 2%), is kept as an other_adjustment with the vendor's factor.
Everything else is the vendor's 3-decimal rounding.
"""
import hashlib, json, math, os, zipfile
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

SOURCE = "sharadar"
PRICE_TABLES = {"SEP": "stock", "SFP": "fund"}
BULK_FILES = {"tickers": "tickers.csv.zip", "stocks": "stocks.csv.zip", "funds": "funds.csv.zip",
              "actions": "actions.csv.zip", "fundamentals": "fundamentals.csv.zip"}
HALF_TICK = 0.0005                      # the vendor rounds prices to 3 decimals
SPLIT_MIN = math.log(1.005)             # a factor step smaller than this is never a split
OTHER_KINDS = ("spinoff", "spinoffdividend", "spunofffrom", "adrratiosplit")
OTHER_ALWAYS = math.log(1.02)           # an unexplained total-return step this large is kept whatever its cause


# --- reading the vendor's files ------------------------------------------------------------------------------------

def file_info(path):
    """size, modification time and sha256 of a vendor file, for the loads table."""
    p = Path(path)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    st = p.stat()
    return dict(path=str(p), size=st.st_size, modified=st.st_mtime, sha256=h.hexdigest())


def _read_csv(path, columns, dtypes=None):
    import pyarrow as pa, pyarrow.csv as pc
    types = {c: pa.string() for c in columns}
    types.update({c: pa.float64() for c in (dtypes or {})})
    opts = pc.ConvertOptions(include_columns=columns, column_types=types)
    if str(path).endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            with z.open(z.namelist()[0]) as f:
                t = pc.read_csv(f, convert_options=opts, read_options=pc.ReadOptions(block_size=1 << 26))
    else:
        t = pc.read_csv(path, convert_options=opts, read_options=pc.ReadOptions(block_size=1 << 26))
    return t.to_pandas()


PRICE_COLS = ["ticker", "date", "open", "high", "low", "close", "volume", "closeadj", "closeunadj"]
TICKER_COLS = ["table", "permaticker", "ticker", "name", "exchange", "isdelisted", "category", "cusips", "figi",
               "famaindustry", "sector", "industry", "sicsector", "sicindustry", "firstpricedate", "lastpricedate"]
ACTION_COLS = ["date", "action", "ticker", "value"]
FUND_COLS = ["ticker", "dimension", "date", "reportperiod", "sharesbas", "sharefactor", "shareswa", "marketcap"]


def read_files(files):
    """Vendor frames from file paths: {'tickers': ..., 'stocks': ..., 'funds': ..., 'actions': ..., 'fundamentals': ...}.
    Any may be missing (an increment carries only what changed)."""
    out = {}
    if "tickers" in files:
        out["tickers"] = _read_csv(files["tickers"], TICKER_COLS)
    for k in ("stocks", "funds"):
        if k in files:
            out[k] = _read_csv(files[k], PRICE_COLS, dtypes=PRICE_COLS[2:])
    if "actions" in files:
        out["actions"] = _read_csv(files["actions"], ACTION_COLS)
    if "fundamentals" in files:
        f = _read_csv(files["fundamentals"], FUND_COLS, dtypes=["sharesbas", "sharefactor", "shareswa", "marketcap"])
        out["fundamentals"] = f[f.dimension.isin(["ARQ", "ARY"])].reset_index(drop=True)
    return out


def bulk_files(directory):
    return {k: str(Path(directory).expanduser() / v) for k, v in BULK_FILES.items()
            if (Path(directory).expanduser() / v).exists()}


# --- securities ----------------------------------------------------------------------------------------------------

def securities(tickers):
    """One row per security with prices (SEP stocks, SFP funds), keyed by permaticker."""
    t = tickers[tickers["table"].isin(PRICE_TABLES)].copy()
    t = t.replace({"": None, "N/A": None})
    out = pd.DataFrame({
        "security_id": t.permaticker, "ticker": t.ticker, "name": t.name, "asset_class": t["table"].map(PRICE_TABLES),
        "category": t.category, "exchange": t.exchange, "sector": t.sector, "industry": t.industry,
        "famaindustry": t.famaindustry, "sicsector": t.sicsector, "sicindustry": t.sicindustry,
        "isdelisted": t.isdelisted, "first_price_date": t.firstpricedate, "last_price_date": t.lastpricedate,
        "cusips": t.cusips, "figi": t.figi})
    return out.drop_duplicates("security_id").reset_index(drop=True)


def ticker_map(tickers, table):
    t = tickers[tickers["table"] == table]
    return dict(zip(t.ticker, t.permaticker))


# --- splits from the vendor's own factor ---------------------------------------------------------------------------

def _factor(px):
    """The vendor's split factor on each row, and its rounding tolerance (relative, in log terms)."""
    f = px.closeunadj / px.close
    tol = HALF_TICK / px.close + HALF_TICK / px.closeunadj
    return f, tol


def _nice(s, tol):
    """A split ratio from a factor step: the simplest fraction within the rounding (a/b with b <= 1000)."""
    for den in (1, 2, 3, 4, 5, 8, 10, 20, 25, 50, 100, 1000):
        fr = Fraction(s).limit_denominator(den)
        if fr > 0 and abs(math.log(float(fr) / s)) <= max(tol, 1e-6):
            return float(fr)
    return s


def detect_splits(px, actions=None):
    """The splits the vendor applied, from the steps in its split factor: security_id, ex_date, ratio, how.

    px: vendor rows with security_id, date, close, closeunadj, sorted by security and date.
    A step counts when it is larger than four times its rounding and than 0.5%. Its ratio is the ACTIONS split value
    within 10 days that fits it (how='action'), else the simplest fraction that fits (how='factor')."""
    px = px[(px.close > 0) & (px.closeunadj > 0)]
    f, tol = _factor(px)
    same = px.security_id.eq(px.security_id.shift())
    step = np.log(f.shift() / f)
    noise = tol + tol.shift()
    hit = same & (step.abs() > np.maximum(4 * noise, SPLIT_MIN))
    cand = pd.DataFrame({"security_id": px.security_id[hit], "ex_date": px.date[hit],
                         "s": np.exp(step[hit]), "noise": noise[hit]})
    if cand.empty:
        return pd.DataFrame(columns=["security_id", "ex_date", "ratio", "how"])
    acts = {}
    if actions is not None and len(actions):
        a = actions[actions.action == "split"]
        for sid, g in a.groupby("security_id"):
            acts[sid] = list(zip(pd.to_datetime(g.date), g.value.astype(float)))
    out = []
    for sid, d, s, nz in cand.itertuples(index=False):
        best, how = None, "factor"
        day = pd.Timestamp(d)
        for ad, v in acts.get(sid, ()):
            if v > 0 and abs((ad - day).days) <= 10 and abs(math.log(v / s)) <= max(4 * nz, 0.01):
                if best is None or abs(math.log(v / s)) < abs(math.log(best / s)):
                    best, how = v, "action"
        out.append((sid, d, best if best is not None else _nice(s, 4 * nz), how))
    return pd.DataFrame(out, columns=["security_id", "ex_date", "ratio", "how"])


def later_factor(rows, splits, date_col="date"):
    """F for each row: the product of the security's split ratios with ex-date after the row's date."""
    rows = rows.reset_index(drop=True)
    F = np.ones(len(rows))
    if splits is None or splits.empty:
        return F
    sp = splits.sort_values(["security_id", "ex_date"])
    by = {sid: (g.ex_date.to_numpy(), g.ratio.to_numpy(dtype=float)) for sid, g in sp.groupby("security_id")}
    for sid, idx in rows.groupby("security_id").indices.items():
        if sid not in by:
            continue
        dates, ratios = by[sid]
        after = np.append(np.cumprod(ratios[::-1])[::-1], 1.0)         # after[i]: product of ratios[i:]
        k = np.searchsorted(dates, rows[date_col].to_numpy()[idx], side="right")
        F[idx] = after[k]
    return F


# --- prices, events, shares ----------------------------------------------------------------------------------------

SEP_CHAR = "\x01"                      # sorts before every character a security id or date can hold


def _keys(security_id, date):
    return (pd.Series(security_id).astype(str).to_numpy() + SEP_CHAR + pd.Series(date).astype(str).to_numpy()).astype(object)


def _locate(pkey, px_sid, sid, date):
    """Positions of the first price row on or after each (sid, date), and whether that row is the same security."""
    pos = np.searchsorted(pkey, _keys(sid, date))
    inside = pos < len(pkey)
    hit = inside.copy()
    hit[inside] = px_sid.to_numpy()[pos[inside]] == pd.Series(sid).to_numpy()[inside]
    return np.where(inside, pos, 0), hit


def _with_ids(frame, mapping):
    out = frame.copy()
    out["security_id"] = out.ticker.map(mapping)
    return out


def normalize(vendor, known_splits=None):
    """Vendor frames -> the store's facts. Returns {table: DataFrame} with the store's columns (plus _tol_* columns
    the diff uses for rounding), and 'problems': rows that could not be placed.

    known_splits: the store's current splits, used for securities this batch carries no factor steps for (an
    increment); splits found in this batch take precedence."""
    out, problems = {}, []
    tickers = vendor.get("tickers")
    if tickers is not None:
        out["securities"] = securities(tickers)
    actions = vendor.get("actions")
    px_parts = []
    for k, table in (("stocks", "SEP"), ("funds", "SFP")):
        if k not in vendor:
            continue
        if tickers is None:
            raise ValueError("prices need the tickers file to map tickers to securities")
        p = _with_ids(vendor[k], ticker_map(tickers, table))
        bad = p.security_id.isna()
        if bad.any():
            problems.append(dict(kind="ticker without a security", table=table, rows=int(bad.sum()),
                                 tickers=sorted(p.ticker[bad].unique())[:50]))
        px_parts.append(p[~bad])
    if actions is not None and tickers is not None:
        sep, sfp = ticker_map(tickers, "SEP"), ticker_map(tickers, "SFP")
        actions = actions.copy()
        actions["security_id"] = actions.ticker.map(sep).fillna(actions.ticker.map(sfp))
        actions = actions[actions.security_id.notna()]
    if not px_parts:
        return dict(out, problems=problems)
    px = pd.concat(px_parts, ignore_index=True)
    px = px[px.closeunadj > 0].copy()
    px["_k"] = _keys(px.security_id, px.date)
    px = px.sort_values("_k").drop(columns="_k").reset_index(drop=True)

    found = detect_splits(px, actions)
    if known_splits is not None and len(known_splits):
        # a batch can only see splits inside its own rows: keep the known splits outside each security's span
        span = px.groupby("security_id").date.agg(["min", "max"])
        k = known_splits.join(span, on="security_id")
        outside = k["min"].isna() | (k.ex_date <= k["min"]) | (k.ex_date > k["max"])
        splits = pd.concat([k.loc[outside, ["security_id", "ex_date", "ratio", "how"]], found], ignore_index=True)
        splits = splits.drop_duplicates(["security_id", "ex_date"], keep="last")
    else:
        splits = found
    out["splits"] = splits.sort_values(["security_id", "ex_date"]).reset_index(drop=True)

    F = later_factor(px, out["splits"])
    f, tol = _factor(px)
    ok = (px.close > 0).to_numpy()
    off = ok & (np.abs(np.log(np.where(ok, f, 1) / F)) > 4 * tol.fillna(0).to_numpy() + 1e-9)
    if off.any():                                  # the vendor's factor disagrees with the splits: use the vendor's
        problems.append(dict(kind="row factor differs from the splits", rows=int(off.sum()),
                             securities=int(px.security_id[off].nunique())))
        F = np.where(off, f, F)
    prices = pd.DataFrame({
        "security_id": px.security_id, "date": px.date,
        "open_traded": px.open * F, "high_traded": px.high * F, "low_traded": px.low * F,
        "close_traded": px.closeunadj, "volume_traded": px.volume / F,
        "_tol_price": HALF_TICK * F + 1e-9, "_tol_volume": np.maximum(1.0, 1e-6 * (px.volume / F).abs()),
        "_F": F,
    })
    out["prices"] = prices

    # the vendor's total-return step, to find dividends and other adjustments and to check ours
    same = px.security_id.eq(px.security_id.shift()).to_numpy()
    q = (px.closeadj.shift() * px.close / (px.closeadj * px.close.shift())).to_numpy()
    q_noise = (HALF_TICK / px.closeadj + HALF_TICK / px.closeadj.shift() + HALF_TICK / px.close
               + HALF_TICK / px.close.shift()).to_numpy()
    cprev = px.closeunadj.shift().to_numpy()

    if actions is not None:
        d = actions[actions.action == "dividend"].copy()
        d["value"] = pd.to_numeric(d.value, errors="coerce")
        d = d[d.value > 0].groupby(["security_id", "date"], as_index=False).value.sum()
        d = d.rename(columns={"date": "ex_date"})
        d["amount_traded"] = d.value * later_factor(d, out["splits"], "ex_date")
        d["_tol_amount"] = 5e-6 * later_factor(d, out["splits"], "ex_date") + 1e-9
        out["dividends"] = d[["security_id", "ex_date", "amount_traded", "_tol_amount"]].reset_index(drop=True)
    divs = out.get("dividends", pd.DataFrame(columns=["security_id", "ex_date", "amount_traded"]))

    # each dividend lands on the security's first price row on or after its ex-date
    pkey = _keys(px.security_id, px.date)
    div_on_row = np.zeros(len(px))
    if len(divs):
        pos, hit = _locate(pkey, px.security_id, divs.security_id, divs.ex_date)
        np.add.at(div_on_row, pos[hit], divs.amount_traded.to_numpy()[hit])
    with np.errstate(divide="ignore", invalid="ignore"):
        q_ours = 1 - div_on_row / cprev
        gap = np.log(q / q_ours)
    near_other = np.zeros(len(px), bool)
    if actions is not None:
        o = actions[actions.action.isin(OTHER_KINDS)]
        if len(o):
            d0 = (pd.to_datetime(o.date) - pd.Timedelta(days=3)).dt.strftime("%Y-%m-%d")
            d1 = (pd.to_datetime(o.date) + pd.Timedelta(days=4)).dt.strftime("%Y-%m-%d")
            a = np.searchsorted(pkey, _keys(o.security_id, d0))
            b = np.searchsorted(pkey, _keys(o.security_id, d1))
            for i, j in zip(a, b):
                near_other[i:j] = True
    big = same & np.isfinite(gap) & (np.abs(gap) > 4 * q_noise) & (
        (near_other & (np.abs(gap) > 1e-3)) | (np.abs(gap) > OTHER_ALWAYS))
    out["other_adjustments"] = pd.DataFrame({
        "security_id": px.security_id[big].to_numpy(), "ex_date": px.date[big].to_numpy(),
        "factor": np.exp(gap[big]), "kind": np.where(near_other[big], "spinoff", "unexplained"),
        "_tol_factor": 4 * q_noise[big] + 1e-6})
    out["_vendor_check"] = pd.DataFrame({"security_id": px.security_id, "date": px.date, "q": q,
                                         "q_noise": q_noise, "same": same})

    fund = vendor.get("fundamentals")
    if fund is not None and tickers is not None:
        sf1 = ticker_map(tickers, "SF1")
        fu = _with_ids(fund, sf1).dropna(subset=["security_id"])
        bas = fu.sharesbas * fu.sharefactor.where(fu.sharefactor > 0, 1.0)
        use_bas = fu.sharesbas > 0
        n = np.where(use_bas, bas, fu.shareswa)
        fu = fu.assign(_n=n, field=np.where(use_bas, "sharesbas", "shareswa"))
        fu = fu[fu._n > 0].rename(columns={"date": "filing_date", "reportperiod": "report_period"}).reset_index(drop=True)
        Fs = later_factor(fu, out["splits"], "filing_date")
        out["shares"] = pd.DataFrame({"security_id": fu.security_id, "filing_date": fu.filing_date,
                                      "dimension": fu.dimension, "report_period": fu.report_period,
                                      "shares_filed": fu._n / Fs, "field": fu.field,
                                      "_tol_shares": np.maximum(1.0, 1e-6 * fu._n / Fs)})
        out["_vendor_marketcap"] = pd.DataFrame({"security_id": fu.security_id, "filing_date": fu.filing_date,
                                                 "dimension": fu.dimension, "marketcap": fu.marketcap})
    out["problems"] = problems
    return out
