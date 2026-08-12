"""Bulk-download orchestration: from-zero warehouse build + incremental update.

Sharadar pre-generates whole-table zips (api.sharadar.com/v1.0/data/<table>?
api_key=...&years=full|10|5 redirects to a time-limited download URL; see
sharadar.com/docs/bulk). `bulk_fromzero` rebuilds every table from the bulk
files; `bulk_update` re-downloads only tables whose server file is newer than
the last one loaded (tracked in a JSON manifest next to the zips), then loads
the deltas with INSERT OR REPLACE.

Tables and their loaders (bulkload.py):
  tickers, stocks, funds, actions, metrics, sp500, fundamentals
"""
import json
import os
import time
import urllib.error
import urllib.request

from . import db

API = "https://api.sharadar.com/v1.0/data/{table}"
DEFAULT_DIR = os.path.expanduser("~/.prime/agent/bulk")
MANIFEST = "_manifest.json"

# table -> (bulk file name, loader name in bulkload module)
BULK_TABLES = {
    "tickers": "load_tickers",
    "stocks": "load_stocks",
    "funds": "load_funds",
    "actions": "load_actions",
    "metrics": "load_metrics",
    "sp500": "load_sp500",
    "fundamentals": "load_fundamentals",
    "descriptions": "load_descriptions",  # vendor field dictionary (all tables)
}

# tables whose contents get wiped before a from-zero load
WIPE_TABLES = ["prices", "sf1", "corporate_actions", "securities_master",
               "metrics", "sp500_membership"]


def _key() -> str:
    k = os.environ.get("SHARADAR_API_KEY", "")
    if k:
        return k
    p = os.path.expanduser("~/.env")
    if os.path.exists(p):
        for line in open(p):
            if line.startswith("SHARADAR_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def bulk_status(table: str) -> dict:
    """Server info (name, size, modified) for the available history windows."""
    url = f"{API.format(table=table)}?api_key={_key()}&status=True"
    req = urllib.request.Request(url, headers={"User-Agent": "data-manager/0.5"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def _download(table: str, file_name: str, dest_dir: str, modified: str) -> str:
    """Download one bulk zip with 429/Retry-After backoff. Returns the path."""
    path = os.path.join(dest_dir, file_name)
    url = f"{API.format(table=table)}?api_key={_key()}&years=full"
    for attempt in range(8):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "data-manager/0.5"})
            with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            return path
        except urllib.error.HTTPError as e:
            wait = max(15, int(e.headers.get("Retry-After", "30")))
            print(f"[bulk] {table}: HTTP {e.code} -> retry in {wait}s", flush=True)
            time.sleep(wait)
        except Exception as e:
            print(f"[bulk] {table}: {type(e).__name__} {e} -> retry", flush=True)
            time.sleep(10)
    raise RuntimeError(f"could not download {table} bulk zip")


def _manifest(dest_dir: str) -> dict:
    p = os.path.join(dest_dir, MANIFEST)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            pass
    return {}


def _save_manifest(dest_dir: str, manifest: dict) -> None:
    json.dump(manifest, open(os.path.join(dest_dir, MANIFEST), "w"), indent=1)


def sync_table(table: str, dest_dir: str, force: bool = False) -> bool:
    """Download <table>'s bulk zip if it changed (or force). Returns whether
    downloaded (and thus whether the loader should run)."""
    os.makedirs(dest_dir, exist_ok=True)
    info = bulk_status(table)
    full = next((f for f in info.get("files", []) if f.get("history") == "full"), None)
    if not full:
        print(f"[bulk] {table}: no full-history bulk file available; skipping")
        return False
    manifest = _manifest(dest_dir)
    seen = manifest.get(table, {})
    if not force and seen.get("modified") == full.get("modified"):
        print(f"[bulk] {table}: up to date ({full.get('modified')}); skipping download")
        return False
    print(f"[bulk] {table}: downloading {full.get('name')} "
          f"({full.get('sizeLabel')}, modified {full.get('modified')})", flush=True)
    path = _download(table, full["name"], dest_dir, full.get("modified"))
    manifest[table] = {"modified": full.get("modified"), "name": full["name"],
                       "size": full.get("size"), "path": path}
    _save_manifest(dest_dir, manifest)
    return True


def _load(table: str, path: str, conn=None) -> int:
    from . import bulkload as BL
    conn = conn or db.connect()
    fn = getattr(BL, BULK_TABLES[table])
    n = fn(path, conn)
    print(f"[bulk] loaded {n:,} rows into {table}", flush=True)
    return n


def bulk_fromzero(dest_dir: str = DEFAULT_DIR, conn=None, pit_asof: str = None,
                  derive: bool = True, pit: bool = True) -> dict:
    """Full rebuild: download every bulk zip, wipe, load, derive, build PIT."""
    from .universe import (build_piotroski, build_quarterly, build_ratios,
                          build_universe_pit_history)
    conn = conn or db.connect()
    print("[bulk] from-zero: downloading all tables (full history)", flush=True)
    for table in BULK_TABLES:
        sync_table(table, dest_dir, force=True)
    print("[bulk] wiping data tables", flush=True)
    for tbl in WIPE_TABLES:
        conn.execute(f"DELETE FROM {tbl}")
    conn.commit()
    counts = {}
    for table in BULK_TABLES:
        path = os.path.join(dest_dir, _manifest(dest_dir).get(table, {}).get("name", f"{table}.csv.zip"))
        counts[table] = _load(table, path, conn)
    if derive:
        print("[bulk] deriving piotroski/quarterly/ratios", flush=True)
        counts["piotroski"] = build_piotroski(conn)
        counts["quarterly"] = build_quarterly(conn)
        counts["ratios"] = build_ratios(conn)
    if pit:
        counts["pit"] = build_universe_pit_history(conn)
    # optimize so a from-zero rebuild yields a performant database
    from . import dbopt as OP
    OP.optimize_db(conn, vacuum=True, quick=True)
    counts["optimized"] = True
    return counts


def bulk_update(dest_dir: str = DEFAULT_DIR, conn=None, tables=None,
                force: bool = False, derive: bool = True, pit: bool = True,
                pit_asof: str = None) -> dict:
    """Incremental update: only re-download + reload tables whose bulk file
    changed since the last sync (manifest), then re-derive local tables."""
    from .universe import (build_piotroski, build_quarterly, build_ratios,
                          build_universe_pit_history)
    conn = conn or db.connect()
    tables = tables or list(BULK_TABLES)
    loaded, skipped = {}, []
    for t in tables:
        if sync_table(t, dest_dir, force=force):
            path = os.path.join(dest_dir, _manifest(dest_dir).get(t, {}).get("name", f"{t}.csv.zip"))
            loaded[t] = _load(t, path, conn)
        else:
            skipped.append(t)
    loaded_core = {t: n for t, n in loaded.items() if t != "descriptions"}
    if derive and loaded_core:
        print("[bulk] deriving piotroski/quarterly/ratios", flush=True)
        loaded_core["piotroski"] = build_piotroski(conn)
        loaded_core["quarterly"] = build_quarterly(conn)
        loaded_core["ratios"] = build_ratios(conn)
    if pit and loaded_core:
        loaded_core["pit"] = build_universe_pit_history(conn)
    loaded = loaded_core
    return {"loaded": loaded, "skipped": skipped}
