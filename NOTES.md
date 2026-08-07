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
