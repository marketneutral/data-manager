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

## Prices: as-of OHLCV + query-time adjustment (2026-08-07)
- `prices` stores **raw as-of OHLCV** (open/high/low/close/volume) plus an
  `adjustment` column = yf `Adj Close / Close` per day (captures splits AND
  dividends). Adjustment at **query time** via `adjusted_prices(ticker, start, end)`
  -> adjusted_open/high/low/close (raw * factor). Volume stays as traded.
- Target window: **10 years** (`update-prices --all --start 2016-08-01`).
- `update-prices`/`update-fundamentals` are now **resumable** (skip fully-covered
  tickers), **resilient** (per-ticker rate-limit/delist errors logged, not fatal),
  and **paced** (0.25-0.5s between tickers) to respect yfinance limits. Run yfinance
  jobs SEQUENTIALLY (parallel jobs trip YFRateLimitError).
- `data-manager status` shows live progress (counts + coverage). Jobs commit
  incrementally, so interruption never loses committed progress and status is live.
- Fix: enrich-sic was failing on zero-padded CIK strings (format {:010d} needs
  int) - now int(cik). SEC submissions gives SIC + SIC description; LEI is mostly
  NULL from SEC (fine; free source).

## Fundamental ratios (2026-08-07)
- New `ratios` table + `data-manager update-ratios --all`: point-in-time snapshot of
  valuation/quality ratios from yfinance `Ticker().info` (free): trailing/forward P/E,
  P/B, P/S, ROE, ROA, net/gross/operating margin, D/E, current ratio, dividend yield,
  market cap, EV, EV/EBITDA, beta, shares outstanding. Resumable (skip if snapshotted
  today), resilient, paced. NOTE units are yfinance-native: margins/ROE as fractions
  (0.34 = 34%), dividend_yield raw fraction (0.0042 = 0.42%).
- Pipeline order (sequential yfinance jobs to respect rate limits):
  1) update-prices --all --start 2016-08-01 (10y as-of OHLCV + adjustment)  [RUNNING]
  2) update-fundamentals --all (Piotroski F-Score, 9 signals)
  3) update-ratios --all (ratio snapshots)

## Pipeline ops (2026-08-07, evening)
- Supervisor chain (`pipeline_supervisor.sh`) auto-runs: prices -> fundamentals -> ratios,
  then writes PIPELINE_DONE + final `status`. Logs: prices.log, fundamentals.log, ratios.log.
- Prices first pass in L/N region ~62-70%; tail is mostly delisted names (each failure ~20-30s).
- After chain completes, run ONE retry pass: `update-prices --all --start 2016-08-01` (resumable
  skip; the dotted-variant fallback reintroduces BRK.B/BF.B-style tickers missed in pass 1).
- Then `python3 final_verify.py` for the completion report (asserts >=75% tickers priced,
  fundamentals>0, ratios>0, sic>2500).
- Expected residual: delisted/failed tickers never recover (documented, not an error).

## FMP (Financial Modeling Prep) — evaluated 2026-08-07 overnight
- Key: FMP_API_KEY in ~/.env. Provider: providers/fmp.py (FMPProvider). Auto-selected
  automatically (universe.py `_default_data_provider`) when the key is present.
- Cross-validated vs yfinance on AAPL: F-score 7 identical (FY2025), same ROA/CFO,
  matching price adjustment factors. FMP gives 10 annual fiscal years vs yfinance ~5,
  cleaner statement items, proper TTM ratios (P/E, P/B, P/S, ROE, margins, D/E,
  current ratio, dividend yield, beta).
- PRICE HISTORY IS TIER-CAPPED: current key returns only ~5y (1,255 rows, 2021-08 ->
  now) despite timeseries=10000. So KEEP yfinance for the 10y prices; use FMP for
  fundamentals/ratios/classifications going forward (or upgrade the FMP tier).
- Cost rule: do NOT bulk-run FMP overnight without approval (paid per-request tiers
  vary). Single-ticker smoke tests only, done 2026-08-07.
- TODO (morning): decide migration - rerun update-fundamentals + update-ratios with
  FMP (provider auto-selected) once approved; regenerates cleaner + longer history.

## FINAL STATE (2026-08-08) - data manager complete
- universe: 2589 (iShares IWV, as-of 2026-08-06)
- figi 2570 / cik 2563 / sic 2561 / sic_description 2561 / lei 5 (documented gap)
- classifications: 2589 (industry 1302; rest = non-equity/delisted artifacts documented)
- prices: 5,622,249 rows, 2577 tickers, 2016-08-01 -> 2026-08-07 (as-of OHLCV + adjustment,
  query-time adjusted_prices()); 12 residuals = futures/cash-collateral artifacts + delisted
- fundamentals (FMP annual, 10y): 24,093 rows, 2574 tickers, 9 F-score components + score
- ratios (FMP TTM): 2589 snapshots
- quarterly_statements (FMP, ~10y): 93,798 rows, 2574 tickers, 2006-06-30 -> 2026-07-15
- FMP = primary source now (fixed cost, within 750 calls/min); yfinance kept only for 10y prices
- spec sheet: data_quality_report.html (coverage, missingness, timeframes) - OPEN
- final_verify: OVERALL OK


## FMP-ONLY CONVERSION (2026-08-08)
- Removed all yfinance code: deleted `providers/yfinance.py` and its tests; dropped the
  `yfinance` + `pandas` deps from pyproject.toml; removed yfinance references from
  universe.py, cli.py, ishares.py, financialdatasets.py, scratch scripts, README.
- `_default_data_provider()` now ALWAYS returns FMPProvider (no auto-selection).
- Prices are now **as-traded daily OHLCV** (split jumps preserved) from FMP's
  `/stable/historical-price-eod/non-split-adjusted` endpoint (VERIFIED on the Premium
  key: TSLA 891.30 -> 296.07 at the 2022-08-25 3:1 split; SHOP 350.26 -> 33.05 at the
  2022-06-29 10:1 split; as-traded volume too).
  - GOTCHA: the endpoint mislabels raw fields adjOpen/adjHigh/adjLow/adjClose; those ARE
    the as-traded values.
  - GOTCHA: it only responds on the `/stable/` host prefix; `/api/v3/...non-split-adjusted`
    returns [].
  - `adjustment` = FMP split+dividend-adjusted adjClose (historical-price-full) / as-traded
    close -> same Adj Close / Close semantics as before.
- prices table was cleared and re-pulled from FMP (single source, no mixed provenance).
  Consequence: FMP depth in this environment is ~5y (2021-08-10 -> now), so the 10y price
  window from yfinance is retired until FMP deepens (email to FMP support sent re: Premium
  30y depth).

## FMP price depth: ~5y default vs full range (2026-08-11)

FMP's historical endpoints return **only the default window (~1,254 rows
~= 5 years)** when called without range params — even with `timeseries=10000`.
This was initially mistaken for a per-plan depth cap (Premium advertises
"30 years"; a support email was drafted but never needed). The real mechanism:
**explicit `from`/`to` unlocks the full plan depth.**

- `/stable/historical-price-eod/non-split-adjusted?symbol=X&from=2016-08-01&to=2026-08-10`
  -> 2,520 rows (10y as-traded)
- `/api/v3/historical-price-full/X?from=2016-08-01&to=2026-08-10` -> same depth

Gotchas: the `/stable/` endpoint honors `from`/`to` but **ignores**
`start_date`/`end_date`. `update-prices` has sent the range since 2026-08-11;
the 27 ETF extras were repulled to `2016-08-01` (67,593 rows; XLC correctly
starts at its 2018-06 inception). The stock universe was repulled the same way.

