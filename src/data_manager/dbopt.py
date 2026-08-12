"""SQLite warehouse optimization (run after big builds, before research).

Steps (all idempotent; safe to re-run):
  1. optional consistent backup (sqlite backup API) to a given path
  2. wal_checkpoint(TRUNCATE)   -- fold the WAL back into the main file
  3. create schema indexes       -- db.connect() runs CREATE INDEX IF NOT EXISTS
  4. ANALYZE                     -- planner statistics after bulk loads
  5. integrity_check             -- full unless quick=True (quick_check)
  6. VACUUM                      -- rebuild/defragment unless vacuum=False
  7. final checkpoint + report   -- sizes, pages, indexes, freelist

`bulk_fromzero` calls this after loads + derivations so a from-zero rebuild
produces an optimized database without extra manual steps.
"""
import os
import sqlite3

from . import db


def backup(conn, dest: str) -> str:
    """Consistent full backup of the live DB (WAL-inclusive) to `dest`."""
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)
    dst = sqlite3.connect(dest)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return dest


def optimize_db(conn=None, backup_path: str = None, vacuum: bool = True,
                quick: bool = False, verbose: bool = True) -> dict:
    """Run the full optimization pass; returns a report dict."""
    conn = conn or db.connect()
    report = {}
    if backup_path:
        dest = backup(conn, backup_path)
        report["backup"] = dest
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # indexes are ensured by db.connect() (SCHEMA has IF NOT EXISTS)
    conn.execute("ANALYZE")
    t0 = _now()
    if quick:
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
    else:
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    report["integrity"] = check
    report["check_s"] = round(_now() - t0, 1)
    if vacuum:
        t0 = _now()
        conn.execute("VACUUM")
        report["vacuum_s"] = round(_now() - t0, 1)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    report["page_count"] = conn.execute("PRAGMA page_count").fetchone()[0]
    report["freelist"] = conn.execute("PRAGMA freelist_count").fetchone()[0]
    report["size_mb"] = round(os.path.getsize(db.DEFAULT_DB) / 1e6, 1)
    report["indexes"] = [r[1] for r in
                         (list(conn.execute("PRAGMA index_list('prices')")) +
                          list(conn.execute("PRAGMA index_list('sf1')")) +
                          list(conn.execute("PRAGMA index_list('universe_pit')")))]
    if verbose:
        print("=== optimize-db report ===")
        for k, v in report.items():
            print(f"  {k}: {v}")
    return report


def _now():
    import time
    return time.time()
