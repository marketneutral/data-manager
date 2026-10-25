"""Point-in-time audit of the warehouse: invariants a backtest relies on, checked on the live data.

    data-manager pit-audit            # run every check, record the run, exit 1 on any error

Each check returns (status, value, detail). `error` means a point-in-time guarantee is broken and
results built on the warehouse should not be trusted until it is fixed; `warn` means a known
limitation drifted past its usual size. Every run is appended to `pit_audit_runs` (a ledger: rows
are never updated or deleted), so a refresh that breaks an invariant is visible afterwards.

The thresholds come from the 2026-10-25 audit of the full history (see AGENTS.md, "Point-in-time
contract"): on a healthy warehouse every check passes with room to spare.
"""
from __future__ import annotations

import datetime as dt
import json
import math

from . import db

ERROR, WARN, OK = "error", "warn", "ok"


def _one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def check_member_price_floor(conn, min_price=2.0):
    """No member day has a same-day as-traded close below the price floor."""
    n = _one(conn, "SELECT COUNT(*) FROM universe_pit u JOIN prices p ON p.ticker = u.ticker AND p.date = u.as_of"
                   " WHERE p.close < ?", (min_price,))[0]
    return (OK if n == 0 else ERROR), n, f"member-days with same-day close < ${min_price:g}"


def check_member_profile_is_same_day(conn):
    """When a member has a quote that day, its stored profile is that day's quote, not a stale one."""
    n = _one(conn, "SELECT COUNT(*) FROM universe_pit u JOIN prices p ON p.ticker = u.ticker AND p.date = u.as_of"
                   " WHERE p.close IS NOT NULL AND abs(u.price - p.close) > 1e-9 * abs(p.close)")[0]
    return (OK if n == 0 else ERROR), n, "member-days whose stored price is not the same-day close (stale profile)"


def check_mcap_continuous_across_splits(conn, min_share=0.95):
    """A split changes price and share count, not market cap: member mcap is flat across split days."""
    rows = conn.execute(
        "WITH m AS (SELECT ticker, as_of, mcap, LAG(mcap) OVER (PARTITION BY ticker ORDER BY as_of) AS prev,"
        "                  LAG(as_of) OVER (PARTITION BY ticker ORDER BY as_of) AS prev_day"
        "           FROM universe_pit WHERE ticker IN (SELECT DISTINCT ticker FROM corporate_actions WHERE action = 'split'))"
        " SELECT m.mcap, m.prev FROM m JOIN corporate_actions s ON s.ticker = m.ticker AND s.date = m.as_of"
        " WHERE s.action = 'split' AND CAST(s.value AS REAL) > 0 AND m.prev_day >= date(m.as_of, '-10 days')").fetchall()
    pairs = [(a, b) for a, b in rows if a and b]
    if not pairs:
        return WARN, None, "no split days with a member on both sides"
    flat = sum(abs(math.log(a / b)) < math.log(1.15) for a, b in pairs) / len(pairs)
    return (OK if flat >= min_share else ERROR), round(flat, 4), f"share of {len(pairs)} split days with member mcap within 15% across the split (needs >= {min_share})"


def check_mcap_matches_vendor(conn, min_share=0.97):
    """PIT mcap agrees with SF1's own marketcap (computed by the vendor at the filing date)."""
    rows = conn.execute("SELECT u.mcap, s.marketcap FROM sf1 s JOIN universe_pit u ON u.ticker = s.ticker AND u.as_of = s.date"
                        " WHERE s.dimension = 'ARQ' AND s.marketcap > 0 AND u.mcap > 0").fetchall()
    if not rows:
        return WARN, None, "no ARQ report dates on member days"
    share = sum(abs(math.log(a / b)) < math.log(1.05) for a, b in rows) / len(rows)
    return (OK if share >= min_share else ERROR), round(share, 4), f"share of {len(rows)} ARQ report dates with PIT mcap within 5% of SF1 marketcap (needs >= {min_share})"


def check_shares_pit_split_consistent(conn):
    """shares_pit rows divide the restated count by exactly the splits after from_date."""
    n = _one(conn, "SELECT COUNT(*) FROM shares_pit WHERE abs(shares * split_factor - restated_shares) > 1e-6 * restated_shares")[0]
    return (OK if n == 0 else ERROR), n, "shares_pit rows where shares x split_factor != restated_shares"


def check_ar_filed_after_period(conn, max_share=0.001):
    """As-reported SF1 rows are dated by their filing, which cannot precede the period end."""
    tot, early = _one(conn, "SELECT COUNT(*), SUM(date < reportperiod) FROM sf1 WHERE dimension IN ('ARQ','ARY','ART')")
    share = (early or 0) / tot if tot else 0.0
    return (OK if share <= max_share else ERROR), round(share, 6), f"{early} of {tot} AR rows dated before their period end (needs <= {max_share})"


def check_derived_knowledge_dates(conn):
    """fundamentals and quarterly_statements carry the filing date each row became known."""
    missing = sum(_one(conn, f"SELECT COUNT(*) FROM {t} WHERE filing_date IS NULL")[0]
                  for t in ("fundamentals", "quarterly_statements"))
    return (OK if missing == 0 else ERROR), missing, "derived fundamentals rows without a filing_date"


def check_universe_covers_prices(conn):
    """universe_pit is built through the latest price date."""
    p, u = _one(conn, "SELECT (SELECT MAX(date) FROM prices), (SELECT MAX(as_of) FROM universe_pit)")
    return (OK if p == u else ERROR), u, f"universe_pit ends {u}, prices end {p}"


def check_freshness(conn, max_age_days=10, today=None):
    """The warehouse has been refreshed recently (a warning, not a point-in-time error)."""
    p = _one(conn, "SELECT MAX(date) FROM prices")[0]
    today = today or dt.date.today()
    age = (today - dt.date.fromisoformat(p)).days if p else None
    return (OK if age is not None and age <= max_age_days else WARN), age, f"days since the latest price ({p})"


def check_member_junk_quotes(conn, max_share=0.0005):
    """Few member days carry a zero-volume quote or a junk adjustment factor."""
    tot, bad = _one(conn, "SELECT COUNT(*), SUM(p.volume = 0 OR p.adjustment <= 0 OR p.adjustment >= 1e6)"
                          " FROM universe_pit u JOIN prices p ON p.ticker = u.ticker AND p.date = u.as_of")
    share = (bad or 0) / tot if tot else 0.0
    return (OK if share <= max_share else WARN), round(share, 6), f"{bad} of {tot} member-days with zero volume or a junk adjustment (warn above {max_share})"


CHECKS = [check_member_price_floor, check_member_profile_is_same_day, check_mcap_continuous_across_splits,
          check_mcap_matches_vendor, check_shares_pit_split_consistent, check_ar_filed_after_period,
          check_derived_knowledge_dates, check_universe_covers_prices, check_freshness, check_member_junk_quotes]


def run(conn=None, checks=None, record=True, verbose=True) -> dict:
    """Run the checks; append them to pit_audit_runs; return {'status': worst, 'results': [...]}."""
    conn = conn or db.connect()
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []
    for fn in checks or CHECKS:
        try:
            status, value, detail = fn(conn)
        except Exception as exc:                        # a check that cannot run is itself an error
            status, value, detail = ERROR, None, f"{type(exc).__name__}: {exc}"
        results.append({"check": fn.__name__.removeprefix("check_"), "status": status, "value": value, "detail": detail})
        if verbose:
            print(f"[pit-audit] {status:5s} {fn.__name__.removeprefix('check_'):34s} {value!s:>10}  {detail}", flush=True)
    worst = ERROR if any(r["status"] == ERROR for r in results) else WARN if any(r["status"] == WARN for r in results) else OK
    if record:
        conn.executemany("INSERT INTO pit_audit_runs (run_at, check_name, status, value, detail) VALUES (?,?,?,?,?)",
                         [(run_at, r["check"], r["status"], json.dumps(r["value"]), r["detail"]) for r in results])
        conn.commit()
    return {"status": worst, "run_at": run_at, "results": results}
