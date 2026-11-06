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

from .keys import DAY, Keys, day_number

SOURCE = "sharadar"
PRICE_TABLES = {"SEP": "stock", "SFP": "fund"}
BULK_FILES = {"tickers": "tickers.csv.zip", "stocks": "stocks.csv.zip", "funds": "funds.csv.zip",
              "actions": "actions.csv.zip", "fundamentals": "fundamentals.csv.zip"}
HALF_TICK = 0.0005                      # the vendor rounds prices to 3 decimals
SPLIT_MIN = math.log(1.005)             # a factor step smaller than this is never a split
OTHER_KINDS = ("spinoff", "spinoffdividend", "spunofffrom", "adrratiosplit")
OTHER_ALWAYS = math.log(1.02)           # an unexplained total-return step this large is kept whatever its cause
CONFIRM_MIN = math.log(1.2)             # a factor step with no ACTIONS row is a split only if at least this large
CONFIRM_FIT = math.log(1.5)             # and the as-printed price moves by its ratio, within this


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
    within 10 days that fits it (how='action'). With no such row it is a split only if it is 20% or more and the
    as-printed price moves by its ratio (within 1.5x) that day (how='factor'); otherwise it is the vendor's glitch (a
    day its split-adjusted series was mis-scaled), reported, and its rows keep the vendor's own factor."""
    px = px[(px.close > 0) & (px.closeunadj > 0)]
    f, tol = _factor(px)
    sid = px["_code"] if "_code" in px else px.security_id
    same = sid.eq(sid.shift())
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
    move = np.log(px.closeunadj / px.closeunadj.shift())[hit].to_numpy()       # the as-printed price's move that day
    out, unconfirmed = [], []
    for (sid, d, s, nz), mv in zip(cand.itertuples(index=False), move):
        best, how = None, "factor"
        day = pd.Timestamp(d)
        for ad, v in acts.get(sid, ()):
            if v > 0 and abs((ad - day).days) <= 10 and abs(math.log(v / s)) <= max(4 * nz, 0.01):
                if best is None or abs(math.log(v / s)) < abs(math.log(best / s)):
                    best, how = v, "action"
        if best is None and not (abs(math.log(s)) >= CONFIRM_MIN and abs(mv + math.log(s)) < CONFIRM_FIT):
            unconfirmed.append((sid, d, s))   # no ACTIONS row and the price does not move by it: a vendor glitch
            continue
        out.append((sid, d, best if best is not None else _nice(s, 4 * nz), how))
    found = pd.DataFrame(out, columns=["security_id", "ex_date", "ratio", "how"])
    found.attrs["unconfirmed"] = unconfirmed
    return found


def later_factor(rows, splits, date_col="date"):
    """F for each row: the product of the security's split ratios with ex-date after the row's date."""
    F = np.ones(len(rows))
    if splits is None or splits.empty or not len(rows):
        return F
    K = Keys(rows.security_id, splits.security_id)
    sk = K.key(splits.security_id, splits.ex_date)
    order = np.argsort(sk, kind="stable")
    sk, code = sk[order], sk[order] // DAY
    logr = np.log(splits.ratio.to_numpy(dtype=float)[order])
    # suffix[i]: the sum of log ratios of splits i.. of the same security
    rev = pd.Series(logr[::-1]).groupby(code[::-1]).cumsum().to_numpy()[::-1]
    rk = K.key(rows.security_id, rows[date_col])
    i = np.searchsorted(sk, rk, side="right")          # the first split after the row (in key order)
    inside = i < len(sk)
    hit = inside.copy()
    hit[inside] = code[i[inside]] == rk[inside] // DAY
    F[hit] = np.exp(rev[i[hit]])
    return F


# --- prices, events, shares ----------------------------------------------------------------------------------------

def _locate(pkey, K, sid, date):
    """Positions of the first price row on or after each (sid, date), and whether that row is the same security."""
    ek = K.key(sid, date)
    pos = np.searchsorted(pkey, ek)
    inside = pos < len(pkey)
    hit = inside.copy()
    hit[inside] = (pkey[pos[inside]] // DAY) == (ek[inside] // DAY)
    hit &= (ek // DAY) >= 0
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
    px = px[px.closeunadj > 0].reset_index(drop=True)
    K = Keys(px.security_id, *([actions.security_id] if actions is not None else []))
    pkey = K.key(px.security_id, px.date)
    order = np.argsort(pkey, kind="stable")
    px, pkey = px.iloc[order].reset_index(drop=True), pkey[order]
    px["_code"] = pkey // DAY

    found = detect_splits(px, actions)
    if found.attrs.get("unconfirmed"):
        u = found.attrs["unconfirmed"]
        problems.append(dict(kind="factor step that is not a split", rows=len(u),
                             securities=len({x[0] for x in u}), examples=[(a, b, round(c, 4)) for a, b, c in u[:20]]))
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
        "_tol_price": 2 * HALF_TICK * F + 1e-9,     # both the stored and the new value are rounded
        "_tol_volume": np.maximum(1.0, 1e-6 * (px.volume / F).abs()),
        "_F": F,
    })
    out["prices"] = prices

    # the vendor's total-return step, to find dividends and other adjustments and to check ours
    same = px._code.eq(px._code.shift()).to_numpy()
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
    div_on_row = np.zeros(len(px))
    if len(divs):
        pos, hit = _locate(pkey, K, divs.security_id, divs.ex_date)
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
            a = np.searchsorted(pkey, K.key(o.security_id, d0))
            b = np.searchsorted(pkey, K.key(o.security_id, d1))
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


# --- the nightly increment: what the vendor changed since a date ---------------------------------------------------

API = "https://api.sharadar.com/v1.0/data/{table}"
LIMIT = 100_000                         # the API's most rows per request
INCREMENT_TABLES = {                    # our name -> (vendor table, filter on, its date column for splitting)
    "stocks": ("SEP", "lastupdated", "date"), "funds": ("SFP", "lastupdated", "date"),
    "fundamentals": ("SF1", "lastupdated", "date"), "actions": ("ACTIONS", "date", "date"),
}
ACTIONS_LOOKBACK_DAYS = 60              # actions carry no lastupdated: take the recent ones


def _api_get(table, params, retries=6):
    import csv, io, time, urllib.error, urllib.request
    from ..bulk import _key
    url = API.format(table=table) + f"?api_key={_key()}&format=csv" + "".join(f"&{k}={v}" for k, v in params.items())
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "data-manager/store"}), timeout=300) as r:
                return list(csv.reader(io.StringIO(r.read().decode())))
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(max(5, int(e.headers.get("Retry-After", "15"))))
                continue
            raise RuntimeError(f"{table}: HTTP {e.code} {e.read()[:200]!r}") from None
        except Exception:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{table}: no answer after {retries} tries")


def _fetch_window(table, params, date_col, lo, hi, out, depth=0):
    """Every row matching params with lo <= date_col <= hi, splitting the window until each request is under the
    API's limit (so nothing depends on the vendor's row order).

    The API silently returns too few rows for a window longer than about a year (measured 2026-11-06: rows of SEP
    updated since 11-05, one request for 1997-2026 gave 19,292 against 153,247 a year at a time, and a year agreed
    with the sum of its months), so callers keep each window within a calendar year."""
    rows = _api_get(table, dict(params, **{f"{date_col}.gte": lo, f"{date_col}.lte": hi, "limit": LIMIT}))
    header, body = rows[0] if rows else [], rows[1:]
    if len(body) < LIMIT:
        out.extend(body)
        return header
    a, b = pd.Timestamp(lo), pd.Timestamp(hi)
    if a >= b:                                       # one day with as many rows as the limit: page within it
        k = 0
        while True:
            page = _api_get(table, dict(params, **{f"{date_col}.gte": lo, f"{date_col}.lte": hi, "limit": LIMIT, "offset": k}))[1:]
            out.extend(page)
            if len(page) < LIMIT:
                return header
            k += LIMIT
    mid = a + (b - a) / 2
    left_hi, right_lo = mid.strftime("%Y-%m-%d"), (mid + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    _fetch_window(table, params, date_col, lo, left_hi, out, depth + 1)
    return _fetch_window(table, params, date_col, right_lo, hi, out, depth + 1)


def fetch_increment(since, dest, today=None):
    """Download what the vendor changed since `since` (a date) into dest/: stocks, funds and fundamentals updated on
    or after it, actions from ACTIONS_LOOKBACK_DAYS before it, and the whole tickers table. Returns {name: path}."""
    import csv
    dest = Path(dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    today = today or pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    files = {}
    for name, (table, filt, date_col) in INCREMENT_TABLES.items():
        start = since if filt != "date" else (pd.Timestamp(since) - pd.Timedelta(days=ACTIONS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        params = {f"{filt}.gte": start} if filt != date_col else {}
        lo = "1990-01-01" if filt != date_col else start
        out, header = [], []
        for y in range(int(lo[:4]), int(today[:4]) + 1):       # one calendar year at most per request (see below)
            a, b = max(lo, f"{y}-01-01"), min(today, f"{y}-12-31")
            header = _fetch_window(table, params, date_col, a, b, out) or header
        path = dest / f"{name}.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(out)
        files[name] = str(path)
    rows = _api_get("TICKERS", {"limit": LIMIT})
    if len(rows) - 1 >= LIMIT:
        raise RuntimeError("the tickers table reached the API's limit: fetch it in pieces")
    with open(dest / "tickers.csv", "w", newline="") as f:
        csv.writer(f).writerows(rows)
    files["tickers"] = str(dest / "tickers.csv")
    return files
