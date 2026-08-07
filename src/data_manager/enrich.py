"""Security-master enrichment: FIGI (OpenFigi), CIK (SEC), SIC + LEI (SEC submissions).

All free.
"""

import time

import httpx

from . import db

OPENFIGI_MAP = "https://api.openfigi.com/v3/mapping"
OPENFIGI_BATCH = 10

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_USER_AGENT = "Research data-manager/0.1 jlarkin@example.com"
SEC_DELAY = 0.15  # be polite to SEC (~6-7 req/sec)


def enrich_figi(conn=None, min_interval: float = 3.0) -> int:
    """Enrich universe with FIGI from OpenFigi. Resumable + rate-limit safe.

    - Only processes tickers missing a FIGI (so re-running continues cleanly).
    - Batches of 10 (OpenFigi free-tier max), paced by `min_interval` seconds
      between requests, with exponential backoff on 429/5xx.
    - Updates the DB incrementally as it goes, so an interrupted run resumes.

    Returns the number of tickers enriched on this run.
    """
    conn = conn or db.connect()
    tickers = [r["ticker"] for r in conn.execute(
        "SELECT ticker FROM universe WHERE figi IS NULL OR figi='' ORDER BY ticker"
    ).fetchall()]
    got = 0
    for i in range(0, len(tickers), OPENFIGI_BATCH):
        batch = tickers[i:i + OPENFIGI_BATCH]
        body = [{"idType": "TICKER", "idValue": t, "exchCode": "US"} for t in batch]
        resp = _openfigi_with_retry(body, min_interval=min_interval)
        for t, job in zip(batch, resp.json()):
            data = job.get("data") or []
            if data:
                conn.execute(
                    "UPDATE universe SET figi=? WHERE ticker=?",
                    (data[0].get("figi"), t),
                )
                got += 1
        time.sleep(min_interval)  # conservative pace
    conn.commit()
    return got


def _openfigi_with_retry(body: list, attempts: int = 8, min_interval: float = 3.0) -> httpx.Response:
    """POST to OpenFigi with exponential backoff on 429/5xx."""
    import random
    for a in range(attempts):
        resp = httpx.post(OPENFIGI_MAP, json=body, timeout=60)
        if resp.status_code in (429, 500, 502, 503, 504):
            wait = max(min_interval, 2 ** a) + random.uniform(0, 1)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def _cik_map() -> dict[str, str]:
    """Return {ticker: str(cik)} from SEC company_tickers.json."""
    resp = httpx.get(SEC_TICKERS, headers={"User-Agent": SEC_USER_AGENT}, timeout=60)
    resp.raise_for_status()
    out = {}
    for row in resp.json().values():
        out[str(row["ticker"]).upper()] = str(row["cik_str"]).zfill(10)
    return out


def enrich_cik(conn=None) -> int:
    """Enrich universe with CIK from SEC. Returns count enriched."""
    conn = conn or db.connect()
    cik_map = _cik_map()
    tickers = [r["ticker"] for r in conn.execute(
        "SELECT ticker FROM universe").fetchall()]
    got = 0
    for t in tickers:
        cik = cik_map.get(t.upper())
        if cik:
            conn.execute("UPDATE universe SET cik=? WHERE ticker=?", (cik, t))
            got += 1
    conn.commit()
    return got


def enrich_sic_lei(conn=None, max_tickers: int | None = None) -> int:
    """Enrich universe with SIC, SIC description, and LEI from SEC submissions.

    One SEC request per company (polite rate limit). Set max_tickers to cap work.
    Returns count enriched.
    """
    conn = conn or db.connect()
    rows = conn.execute(
        "SELECT ticker, cik FROM universe WHERE cik IS NOT NULL ORDER BY ticker"
    ).fetchall()
    if max_tickers:
        rows = rows[:max_tickers]
    got = 0
    for r in rows:
        ticker, cik = r["ticker"], r["cik"]
        try:
            resp = httpx.get(
                SEC_SUBMISSIONS.format(cik=cik),
                headers={"User-Agent": SEC_USER_AGENT},
                timeout=60,
            )
            if resp.status_code == 200:
                d = resp.json()
                conn.execute(
                    "UPDATE universe SET sic=?, sic_description=?, lei=? WHERE ticker=?",
                    (str(d.get("sic") or ""), d.get("sicDescription"),
                     d.get("lei"), ticker),
                )
                got += 1
        except Exception:
            pass
        time.sleep(SEC_DELAY)
    conn.commit()
    return got
