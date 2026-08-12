"""Ken French daily factor returns — US (mba.tuck.dartmouth.edu).

The Data Library's five US *daily factor* files (not the portfolio files —
those are the raw size/B-M/OP/INV buckets; the factors are the ready-made
long-short spreads built from them). Every file is a small CSV zip served
over plain HTTPS; the site sends Last-Modified so refreshes skip unchanged
files via the same JSON manifest the Sharadar bulk pipeline uses.

  file                                        columns                  starts
  F-F_Research_Data_Factors_daily             Mkt-RF, SMB, HML, RF    1926-07-01
  F-F_Research_Data_5_Factors_2x3_daily       Mkt-RF, SMB, HML,       1963-07-01
                                              RMW, CMA, RF
  F-F_Momentum_Factor_daily                   Mom                     1926-11-03
  F-F_ST_Reversal_Factor_daily                ST_Rev                  1926-01-26
  F-F_LT_Reversal_Factor_daily                LT_Rev                  1930-03-20

All returns are PERCENT per day (0.09 = 0.09%). Missing values use the
vendor -99.99 sentinel -> stored NULL. The 3F and 5F files share the same
RF series (verified identical on all 15,854 overlapping dates), so the wide
table stores RF once. `update_french_factors` is the single entry point:
download (manifest-skipped) -> load -> dictionary rows -> snapshots ledger.
"""
from __future__ import annotations

import datetime as dt
import os
import time
import urllib.error
import urllib.request

from . import db

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
DEFAULT_DIR = os.path.expanduser("~/.prime/agent/bulk")   # same cache as bulk.py

# key -> dict(zip file name, db columns, vendor header columns, one-line note)
# db order is the load order (positional, fixed vendor format).
FACTOR_FILES = {
    "3f": dict(
        file="F-F_Research_Data_Factors_daily_CSV.zip",
        cols=["mkt_rf", "smb", "hml", "rf"],
        vcols=["Mkt-RF", "SMB", "HML", "RF"],
        note="Fama/French 3 factors (Mkt-RF, SMB, HML) + daily risk-free rate",
    ),
    "5f": dict(
        file="F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
        cols=["mkt_rf", "smb", "hml", "rmw", "cma", "rf"],
        vcols=["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"],
        note="Fama/French 5 factors (2x3): adds RMW (profitability) and CMA (investment)",
    ),
    "mom": dict(
        file="F-F_Momentum_Factor_daily_CSV.zip",
        cols=["mom"],
        vcols=["Mom"],
        note="Momentum factor (prior 2-12 month return, daily-formed)",
    ),
    "st_rev": dict(
        file="F-F_ST_Reversal_Factor_daily_CSV.zip",
        cols=["st_rev"],
        vcols=["ST_Rev"],
        note="Short-term reversal factor (prior 1-1 month return)",
    ),
    "lt_rev": dict(
        file="F-F_LT_Reversal_Factor_daily_CSV.zip",
        cols=["lt_rev"],
        vcols=["LT_Rev"],
        note="Long-term reversal factor (prior 13-60 month return)",
    ),
}

# Local column definitions for the in-DB data dictionary (`descriptions`
# table, table_name='french_factors'). Refreshed on every factor update;
# the Sharadar load_descriptions only ever touches vendor table names, so
# these rows survive bulk_update.
FRENCH_DESCRIPTIONS = [
    ("french_factors", "date", "Y", "Y", "Trading date",
     "Trading day (YYYY-MM-DD; the source's YYYYMMDD date key converted).", "text"),
    ("french_factors", "mkt_rf", "N", "N", "Market Excess Return (Mkt-RF)",
     "Value-weight return of all US CRSP stocks (NYSE/AMEX/NASDAQ, share code 10/11) minus the daily risk-free rate. Percent per day.", "%"),
    ("french_factors", "smb", "N", "N", "Small Minus Big (SMB)",
     "Average return on the three small size/book-to-market portfolios minus the average return on the three big portfolios (size factor). Percent per day.", "%"),
    ("french_factors", "hml", "N", "N", "High Minus Low (HML)",
     "Average return on the two value (high book-to-market) portfolios minus the two growth portfolios (value factor). Percent per day.", "%"),
    ("french_factors", "rmw", "N", "N", "Robust Minus Weak (RMW)",
     "Average return on the two robust operating-profitability portfolios minus the two weak ones (profitability factor). Percent per day.", "%"),
    ("french_factors", "cma", "N", "N", "Conservative Minus Aggressive (CMA)",
     "Average return on the two conservative-investment portfolios minus the two aggressive ones (investment factor). Percent per day.", "%"),
    ("french_factors", "mom", "N", "N", "Momentum Factor (Mom)",
     "Average return on the two high prior (2-12 month) return portfolios minus the two low ones, formed daily. Percent per day.", "%"),
    ("french_factors", "st_rev", "N", "N", "Short-Term Reversal Factor (ST_Rev)",
     "Average return on the two low prior (1-1 month) return portfolios minus the two high ones, formed daily. Percent per day.", "%"),
    ("french_factors", "lt_rev", "N", "N", "Long-Term Reversal Factor (LT_Rev)",
     "Average return on the two low prior (13-60 month) return portfolios minus the two high ones, formed daily. Percent per day.", "%"),
    ("french_factors", "rf", "N", "N", "Daily Risk-Free Rate (RF)",
     "Simple daily rate that compounds to the one-month Treasury bill rate over the month's trading days. Percent per day.", "%"),
]


def _last_modified(key: str) -> str | None:
    """Server Last-Modified for one factor zip (HEAD; None if unavailable)."""
    url = f"{BASE}/{FACTOR_FILES[key]['file']}"
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "data-manager/0.5"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.headers.get("Last-Modified")
    except Exception as exc:
        print(f"[french] {key}: HEAD {type(exc).__name__}: {exc}", flush=True)
        return None


def _download(key: str, dest_dir: str) -> str:
    """GET one factor zip with 429/Retry-After backoff (bulk-style)."""
    path = os.path.join(dest_dir, FACTOR_FILES[key]["file"])
    url = f"{BASE}/{FACTOR_FILES[key]['file']}"
    for attempt in range(8):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "data-manager/0.5"})
            with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            return path
        except urllib.error.HTTPError as e:
            wait = max(15, int(e.headers.get("Retry-After", "30")))
            print(f"[french] {key}: HTTP {e.code} -> retry in {wait}s", flush=True)
            time.sleep(wait)
        except Exception as e:
            print(f"[french] {key}: {type(e).__name__} {e} -> retry", flush=True)
            time.sleep(10)
    raise RuntimeError(f"could not download Ken French factor zip: {key}")


def sync_factor_file(key: str, dest_dir: str, force: bool = False):
    """Download one factor zip only if the site file changed (Last-Modified
    vs the manifest). Returns the zip path, or None when up to date."""
    from .bulk import _manifest, _save_manifest
    modified = _last_modified(key)
    manifest = _manifest(dest_dir)
    seen = manifest.get(key, {})
    if not force and modified and seen.get("modified") == modified:
        return None
    path = _download(key, dest_dir)
    manifest[key] = {"modified": modified, "name": FACTOR_FILES[key]["file"],
                     "path": path}
    _save_manifest(dest_dir, manifest)
    return path


def update_french_factors(conn=None, dest_dir: str = DEFAULT_DIR,
                          force: bool = False) -> dict:
    """Refresh the `french_factors` table from the Ken French Data Library.

    Downloads only the five daily factor zips whose server Last-Modified
    changed since the last sync (tiny files; force bypasses the skip),
    reloads them with a per-column upsert (each file owns its columns),
    writes the column dictionary rows into `descriptions`, records a
    `snapshots` ledger entry, and returns a report dict
    {downloaded, skipped, loaded: {file: rows}, rows}.
    """
    from .bulkload import load_french_factor_file
    conn = conn or db.connect()
    os.makedirs(dest_dir, exist_ok=True)
    downloaded, skipped, loaded = [], [], {}
    for key in FACTOR_FILES:
        path = sync_factor_file(key, dest_dir, force=force)
        if path is None:
            skipped.append(key)
            continue
        downloaded.append(key)
        n = load_french_factor_file(path, conn,
                                    cols=FACTOR_FILES[key]["cols"],
                                    vcols=FACTOR_FILES[key]["vcols"])
        loaded[key] = n
        print(f"[french] loaded {key}: {n:,} rows", flush=True)
    # in-DB data dictionary rows for this local table
    if FRENCH_DESCRIPTIONS:
        conn.executemany(
            "INSERT OR REPLACE INTO descriptions "
            "(table_name, indicator, isfilter, isprimarykey, title, description, unittype) "
            "VALUES (?,?,?,?,?,?,?)", FRENCH_DESCRIPTIONS)
    # provenance ledger (same table every pull writes to)
    row = conn.execute("SELECT MAX(date) FROM french_factors").fetchone()
    total = conn.execute("SELECT COUNT(*) FROM french_factors").fetchone()[0]
    conn.execute(
        "INSERT INTO snapshots (source, pulled_at, as_of, row_count) VALUES (?,?,?,?)",
        ("french_factors",
         dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(),
         row[0] if row and row[0] else None, total))
    conn.commit()
    return {"downloaded": downloaded, "skipped": skipped,
            "loaded": loaded, "rows": total}
