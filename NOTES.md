# Data Manager Notes

## financialdatasets.ai — known issues & lessons

**Date:** 2026-08-07

### API bug: `limit` hard-capped at 10/page
The OpenAPI spec for `GET /index-funds` documents `limit` (default 50, max 1000),
but in practice the endpoint **always returns exactly 10 holdings per page**,
regardless of the `limit` value passed.

Tested: `limit=10, 50, 100, 500, 1000` — all returned 10 rows.

**Pagination inconsistency:**
- `next_page_url` (cursor pagination) stops after ~1,000 rows.
- `offset`-based pagination continues past 1,000 correctly.

**Impact:** Fetching a fund with ~1,000–2,000 constituents (e.g. IWM, Russell 2000)
took ~100–200 paginated requests instead of the 1–2 implied by `limit=1000`.
This rapidly drained paid credits and, combined with a 402 that interrupted the
job, wasted a large share of purchased credit.

**Status:** Reported to the provider (email, 2026-08-07); awaiting fix. Provider
contact is a personal acquaintance — no refund requested.

### Lessons (operative for the data-manager)
1. **Never bulk-run a paid/credited API without first testing page size and
   estimating total request count + cost, and getting explicit approval.**
2. Prefer free sources (yfinance) for bulk data; use the paid API only where
   it uniquely adds value.
3. Use offset-based pagination, not cursor, for the index-funds endpoint.

### Data pipeline (as decided)
- **Universe (R3000):** financialdatasets.ai — IWB (Russell 1000) + IWM (Russell 2000).
- **Prices / classifications / fundamentals (Piotroski F-Score):** yfinance (free).
- **Storage:** SQLite.

## Update commands

    # Fetch/stored the R3000 universe (uses paid credits — ~300 requests)
    data-manager update-universe

    # Prices for specific tickers (free, yfinance)
    data-manager update-prices --ticker AAPL,MSFT --start 2020-01-01

    # Prices for the whole universe
    data-manager update-prices --all --start 2020-01-01

    # Sector/industry (free)
    data-manager update-classifications --all

    # Piotroski F-Score fundamentals (free)
    data-manager update-fundamentals --all

## Universe source: iShares IWV (free) — 2026-08-07
The R3000 universe now comes from the **free iShares IWV holdings CSV** (not the paid API).
- Endpoint: `ishares.com/us/products/239714/ishares-russell-3000-etf/latest-holdings.csv`
- Count: **2,589 unique tickers**, with ticker/name/sector/weight.
- Provider: `data_manager.providers.ishares.ISharesProvider` (default for `update-universe`).
- The paid financialdatasets.ai provider is kept as an alternative but not the default.
- Saves credit AND gives sector classification for free.

## Security-master enrichment (replicable)

The universe (security master) can be enriched with identifiers & classifications.
All free. Each step is idempotent/resumable and can be re-run to update.

Set up the repo env once:

    cd ~/dev/data-manager
    uv venv
    uv pip install -e .

Run from the repo (via the uv venv):

    # 1. Universe (R3000) — free iShares IWV source
    uv run data-manager update-universe

    # 2. CIK from SEC company_tickers.json (fast, 1 request)
    uv run data-manager enrich-cik

    # 3. FIGI from OpenFigi (batches of 10; rate-limited ~2-5 req/sec, so
    #    this takes ~10-15 min and is resumable — re-run to finish any stragglers)
    uv run data-manager enrich-figi

    # 4. SIC + SIC description + LEI from SEC submissions (1 request per company;
    #    k rate-limited politely; ~25 min for the whole R3000)
    uv run data-manager enrich-sic

## Identifiers / classifications overview
| Field | Source | Free |
|---|---|---|
| Ticker, Name, Sector (GICS) | iShares IWV holdings CSV | yes |
| CIK | SEC company_tickers.json | yes |
| FIGI | OpenFigi mapping API | yes |
| SIC + SIC description | SEC submissions | yes |
| LEI | SEC submissions | yes |
| ISIN | not freely available (OpenFigi no longer returns it) | no |
| NAICS | not freely available from SEC/OpenFigi; would need 10-K parsing | no |

## Known API gotchas
- **OpenFigi**: batch max 10 jobs/request (11+ returns 413); aggressive rate limit
  (429 after ~10-25 req/min) — retry with backoff, pace ~3s between, make resumable.
- **SEC**: requires a descriptive User-Agent header; be polite (~6-7 req/sec).
