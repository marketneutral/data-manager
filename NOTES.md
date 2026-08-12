# Data Manager Notes (Sharadar era)

## Fresh start — 2026-08-11 (FMP removed)

- Sharadar is the sole data provider (`SHARADAR_API_KEY` in ~/.env).
- FMP provider/`tests/test_providers_fmp.py` deleted; FMP-era files, logs, docs,
  and the PRE-WIPE database are archived under `archive/fmp-era/`
  (`data_manager.fmp-era.db` = the full FMP-era DB backup).
- `data_manager.db` was wiped and rebuilt from zero (fresh schema, no rows).
- `_default_data_provider()` now ALWAYS returns SharadarProvider (no env gate).

## Sharadar column semantics (verified live 2026-08-11, paid key)

- `stocks` and `funds` tables have identical columns: date, open, high, low,
  close, closeadj, closeunadj, ticker, volume, lastupdated.
- open/high/low/close are **split-adjusted** (AAPL 2016-08-01 close=26.512 vs
  as-traded 106.05; VXX post-reverse-splits close=1770.24 vs as-traded 27.66).
- closeunadj = as-traded close; closeadj = split+dividend fully adjusted.
- No openunadj/highunadj/lowunadj columns -> as-traded OHLC recovered by scaling
  with the same-row split factor (closeunadj/close).
- Provider stores: close = closeunadj, adjustment = closeadj/closeunadj, so
  close x adjustment -> closeadj (identical semantics to FMP adjClose/as-traded).
- Cross-validation: on VXX's 10 largest FMP-vs-Sharadar discrepancies (2-6%),
  Sharadar matched Yahoo adjusted exactly (0.00% off) on all 10; FMP was the
  outlier. Sharadar as-traded is the better source.
- S&P500 membership: unscoped sp500 pull is capped to ~1 year by the API; per-
  ticker pulls (ticker=AAPL) return full history (AAPL back to 1982-11-30).
  `update-sp500` stores both.

## GICS sector mapping (providers/sharadar.py::SHARADAR_TO_GICS)

Sharadar's own taxonomy -> 11 GICS labels. Unmapped labels -> None (fails
closed; excluded from the eqrm stock factor universe). Verify against live
sector values periodically. AAPL: Technology -> Information Technology.

## Pipeline (fresh build, sequential; ~15-20k requests on the paid key)

    # in repo: uv run data-manager ...
    update-universe                                   # iShares IWV, free, 1 req
    update-master --all                               # securities master
    update-actions --all                              # corporate actions
    update-prices --all --start 1996-01-01            # as-of OHLCV + adjustment
    update-classifications --all --force              # GICS sector/industry
    update-fundamentals --all --force                 # Piotroski F-Score (SF1 AR)
    update-quarterly --all --force                    # SF1 quarterly statements
    update-ratios --all --force                       # ratio snapshots
    update-sp500                                      # S&P500 membership
    # ETF extras (funds table; NOT in universe):
    update-master --ticker <etf list>  &&  update-actions --ticker <etf list>
    update-prices --ticker <etf list> --start 1996-01-01 --force
    update-classifications --ticker <etf list> --force

Notes:
- `--force` on fundamentals/quarterly/ratios was added for the migration;
  without it the FMP-era resume-skip (rows exist) blocks a provider refresh.
- Sharadar funds history starts at each fund's Series/inception firstpricedate
  (e.g. VXX Series B 2018-01-25) — series-change gaps are inherent, not loss.
- Watch: SF1 lacks forward_pe/beta (stored NULL); sector map needs a live
  coverage check after the master pull.

## eqrm/risk-model (next)

Point eqrm consumers at the migrated tables; re-run risk-model data-quality
checks (inclusion filter universe count should grow toward ~3,000 with delisted
names excluded by construction). Only THEN consider cancelling anything Sharadar.

## WAREHOUSE MODE — ALL US equities PIT (2026-08-11)

Vision change (user): stop relying on the IWV R3000 snapshot as THE universe.
Sharadar = full PIT warehouse for ALL US stocks; the investable universe is
constructed by us from type / $volume / mcap / price via `build-universe-pit`.

### What the Sharadar v1 API actually serves (verified live today)
- tickers master: whole-table paged (limit/offset) -> 31,742 price-traded
  securities (21,960 stocks incl. 15,639 delisted + 9,782 funds). Few requests.
- actions: whole table = 47,543 rows total (splits, dividends, spinoffs,
  acquisitions, ticker changes, delist + bankruptcy reasons - ENRNQ confirms).
- metrics: 13,453 rows, LATEST snapshot only (NOT daily history). Whole table.
- fundamentals (SF1): 112 columns/row; dimensions ARY/MRY/ARQ/MRQ; full history
  ONLY per-ticker (whole-table pull is windowed to last ~2 fiscal years).
  TTM is NOT a fundamentals dimension (returns 0 rows).
- stocks (prices): per-ticker WINDOW CAP of ~7,196 rows / ~28.6y (back to
  1998) regardless of startdate/enddate params -- older firstpricedate metadata
  is not fetchable. ~50k-row response cap -> batches are halved + per-ticker
  coverage verified (short ticks refetched individually).
- Params: ticker= accepts comma lists (caps ~30 tickers/request), limit/offset
  paging works, responses truncate at ~7MB OR 50k rows (newest kept).

### Warehouse pipeline (sharadar_warehouse.sh) - runs sequentially
update-master/actions/metrics --all (whole table), update-sf1 --all (879
batches x 4 dims), update-prices --all (1,879 batches, ~48M rows, 1998->now,
as-traded + adjustment), ETF extras, update-sp500, then LOCAL derivations:
build-piotroski / build-quarterly / build-ratios (from the sf1 mirror, ZERO
remote requests), build-universe-pit.

### PIT universe (build-universe-pit)
As of a date D, member iff: category in {Domestic Common Stock (+ Primary/
Secondary classes)} [--types], isdelisted=N or lastpricedate>=D, last as-traded
close >= $2 [--min-price], quote age <= 10d [--max-quote-age], trailing
avg $volume (close*volume over --lookback 20d) >= $5M [--min-dvol] with >=10
days [--min-dvol-days], PIT market cap = close x latest as-reported shareswa
(ARQ/ARY<=D) >= $300M [--min-mcap]. Stored in universe_pit(as_of, ticker, ...).
Defaults are Russell-styled; re-run with different thresholds to compare.
# Data Manager Notes (Sharadar era)

## Fresh start — 2026-08-11 (FMP removed)

- Sharadar is the sole data provider (`SHARADAR_API_KEY` in ~/.env).
- FMP provider/`tests/test_providers_fmp.py` deleted; FMP-era files, logs, docs,
  and the PRE-WIPE database are archived under `archive/fmp-era/`
  (`data_manager.fmp-era.db` = the full FMP-era DB backup).
- `data_manager.db` was wiped and rebuilt from zero (fresh schema, no rows).
- `_default_data_provider()` now ALWAYS returns SharadarProvider (no env gate).

## Sharadar column semantics (verified live 2026-08-11, paid key)

- `stocks` and `funds` tables have identical columns: date, open, high, low,
  close, closeadj, closeunadj, ticker, volume, lastupdated.
- open/high/low/close are **split-adjusted** (AAPL 2016-08-01 close=26.512 vs
  as-traded 106.05; VXX post-reverse-splits close=1770.24 vs as-traded 27.66).
- closeunadj = as-traded close; closeadj = split+dividend fully adjusted.
- No openunadj/highunadj/lowunadj columns -> as-traded OHLC recovered by scaling
  with the same-row split factor (closeunadj/close).
- Provider stores: close = closeunadj, adjustment = closeadj/closeunadj, so
  close x adjustment -> closeadj (identical semantics to FMP adjClose/as-traded).
- Cross-validation: on VXX's 10 largest FMP-vs-Sharadar discrepancies (2-6%),
  Sharadar matched Yahoo adjusted exactly (0.00% off) on all 10; FMP was the
  outlier. Sharadar as-traded is the better source.
- S&P500 membership: unscoped sp500 pull is capped to ~1 year by the API; per-
  ticker pulls (ticker=AAPL) return full history (AAPL back to 1982-11-30).
  `update-sp500` stores both.

## GICS sector mapping (providers/sharadar.py::SHARADAR_TO_GICS)

Sharadar's own taxonomy -> 11 GICS labels. Unmapped labels -> None (fails
closed; excluded from the eqrm stock factor universe). Verify against live
sector values periodically. AAPL: Technology -> Information Technology.

## Pipeline (fresh build, sequential; ~15-20k requests on the paid key)

    # in repo: uv run data-manager ...
    update-universe                                   # iShares IWV, free, 1 req
    update-master --all                               # securities master
    update-actions --all                              # corporate actions
    update-prices --all --start 1996-01-01            # as-of OHLCV + adjustment
    update-classifications --all --force              # GICS sector/industry
    update-fundamentals --all --force                 # Piotroski F-Score (SF1 AR)
    update-quarterly --all --force                    # SF1 quarterly statements
    update-ratios --all --force                       # ratio snapshots
    update-sp500                                      # S&P500 membership
    # ETF extras (funds table; NOT in universe):
    update-master --ticker <etf list>  &&  update-actions --ticker <etf list>
    update-prices --ticker <etf list> --start 1996-01-01 --force
    update-classifications --ticker <etf list> --force

Notes:
- `--force` on fundamentals/quarterly/ratios was added for the migration;
  without it the FMP-era resume-skip (rows exist) blocks a provider refresh.
- Sharadar funds history starts at each fund's Series/inception firstpricedate
  (e.g. VXX Series B 2018-01-25) — series-change gaps are inherent, not loss.
- Watch: SF1 lacks forward_pe/beta (stored NULL); sector map needs a live
  coverage check after the master pull.

## eqrm/risk-model (next)

Point eqrm consumers at the migrated tables; re-run risk-model data-quality
checks (inclusion filter universe count should grow toward ~3,000 with delisted
names excluded by construction). Only THEN consider cancelling anything Sharadar.

## WAREHOUSE MODE — ALL US equities PIT (2026-08-11)

Vision change (user): stop relying on the IWV R3000 snapshot as THE universe.
Sharadar = full PIT warehouse for ALL US stocks; the investable universe is
constructed by us from type / $volume / mcap / price via `build-universe-pit`.

### What the Sharadar v1 API actually serves (verified live today)
- tickers master: whole-table paged (limit/offset) -> 31,742 price-traded
  securities (21,960 stocks incl. 15,639 delisted + 9,782 funds). Few requests.
- actions: whole table = 47,543 rows total (splits, dividends, spinoffs,
  acquisitions, ticker changes, delist + bankruptcy reasons - ENRNQ confirms).
- metrics: 13,453 rows, LATEST snapshot only (NOT daily history). Whole table.
- fundamentals (SF1): 112 columns/row; dimensions ARY/MRY/ARQ/MRQ; full history
  ONLY per-ticker (whole-table pull is windowed to last ~2 fiscal years).
  TTM is NOT a fundamentals dimension (returns 0 rows).
- stocks (prices): per-ticker WINDOW CAP of ~7,196 rows / ~28.6y (back to
  1998) regardless of startdate/enddate params -- older firstpricedate metadata
  is not fetchable. ~50k-row response cap -> batches are halved + per-ticker
  coverage verified (short ticks refetched individually).
- Params: ticker= accepts comma lists (caps ~30 tickers/request), limit/offset
  paging works, responses truncate at ~7MB OR 50k rows (newest kept).

### Warehouse pipeline (sharadar_warehouse.sh) - runs sequentially
update-master/actions/metrics --all (whole table), update-sf1 --all (879
batches x 4 dims), update-prices --all (1,879 batches, ~48M rows, 1998->now,
as-traded + adjustment), ETF extras, update-sp500, then LOCAL derivations:
build-piotroski / build-quarterly / build-ratios (from the sf1 mirror, ZERO
remote requests), build-universe-pit.

### PIT universe (build-universe-pit)
As of a date D, member iff: category in {Domestic Common Stock (+ Primary/
Secondary classes)} [--types], isdelisted=N or lastpricedate>=D, last as-traded
close >= $2 [--min-price], quote age <= 10d [--max-quote-age], trailing
avg $volume (close*volume over --lookback 20d) >= $5M [--min-dvol] with >=10
days [--min-dvol-days], PIT market cap = close x latest as-reported shareswa
(ARQ/ARY<=D) >= $300M [--min-mcap]. Stored in universe_pit(as_of, ticker, ...).
Defaults are Russell-styled; re-run with different thresholds to compare.

## Bulk downloads replace the API fan-out (2026-08-11, evening)

User pointed at sharadar.com/docs/bulk: `years=5|10|full` on ANY /v1.0/data/
<table> endpoint redirects to a pre-generated time-limited .csv.zip. Whole
warehouse now loads from 7 zips (~2GB) rather than ~6k batched API requests.
repo pieces: src/data_manager/bulk.py (bulk-download / bulk-fromzero /
bulk-update + manifest), src/data_manager/bulkload.py (zip -> sqlite loaders).

Verified bulk-file facts:
- tickers zip codes: SEP=stocks(21,960), SFP=funds(9,782), SF1/SF2/SF3B others
- actions bulk = 672,929 rows (14x the API's 47k) incl. delist reasons
- metrics bulk = 31,744 rows with multi-date history (1997-12-31 for some)
- stocks bulk full = 46,263,130 rows, depth still 1998-> (no deeper on this key)
- funds bulk full = 15,473,734 rows; fundamentals bulk = 3,212,940 rows,
  SIX dimensions ARY/MRY/ARQ/MRQ + ART/MRT (TTM), datekey=report date, 1990->
- bulk price volume is AS-TRADED (FAQ text says split-adjusted - trust the file)

GOTCHA (2026-08-11): a hand-written /tmp loader script had a template bug
(`'funds' == 'funds' and DELETE FROM prices`) that wiped the stocks rows after
loading them; reloaded from the zip - nothing lost, zips are the source of
truth. The repo's bulk_fromzero/bulk_update have no such bug (wipe once in
correct order / no deletes at all).

## All-history PIT universe + DB optimization (2026-08-11, night)

- build-universe-pit --history: universe_pit now holds a member row per
  (TRADING DAY, MEMBER) for 1998->now: 13.46M stock-days over 7,187 dates
  (e.g. 2,435 members 2026-08-11; 2,093 on 2020-03-23; 1,399 on 1998-07-01).
  Algorithm: per-ticker pass over its price rows (rolling $volume window +
  SF1 ARQ/ARY shares pointer) -> validity runs merged and expanded onto the
  global trading calendar capped at max_quote_age calendar days past the last
  quote. NOTE the single-date build may differ by ~1% on the same date
  (definitional: history evaluates each day on its own quote; single-date
  evaluates the last quote).
- optimize-db command (src/data_manager/dbopt.py): backup -> WAL checkpoint ->
  schema indexes (idx_prices_date = (date,ticker,close,volume) for PIT scans)
  -> ANALYZE -> integrity_check -> VACUUM -> report. Hooked into bulk_fromzero
  so a from-zero rebuild yields the optimized DB automatically.
- db.connect() now sets synchronous=NORMAL, journal_size_limit=512MB,
  cache_size=256MB, temp_store=MEMORY, mmap_size=128MB.

## Reporting (Quarto)

- `report/data.qmd` -> `quarto render report/data.qmd` -> self-contained
  `report/data.html`: coverage/depth/PIT/quality report read directly from
  `data_manager.db` (kernel `data-manager` = this repo's venv; dev deps
  pandas/numpy/matplotlib/ipykernel).
- Gotcha (learned 2026-08-11): Quarto's jupyter engine executes with cwd =
  the qmd's directory; resolve repo-root paths by walking up until `src/`
  exists, and locate `report.mplstyle` relative to cwd OR `BASE/report`.
  `matplotlib.use("Agg")` after pyplot import also kills figure emission.
- Quality facts surfaced by the first report: 5.5% of price rows are
  volume=0 no-trade days (~301k carry the 8.5e18 adjustment sentinel);
  ~1.8% of traded rows have adjustment>100 (mechanical split/dividend
  tail; use as-traded close for returns); 2 rows have close<=0; GICS
  classifications cover only 2,589 of 21,960 stocks (live coverage check
  still open).

## PIT profile bug found & fixed (2026-08-12)

- BUG: build_universe_pit_history stored the RUN-START quote's price/mcap/dvol for
  the whole validity run. A continuously trading name has ONE run 1998->now, so
  AAPL carried $19.75 (its 1998 close) as its 2026-08-11 profile, MSFT $131.13,
  NVDA $19.25. Membership DATES were correct; the attached profile was not.
  Detected while sanity-checking mcap analytics for the new report (top-12 by
  mcap looked wrong: 'SPCX' above NVDA).
- FIX: universe.py now carries the MOST RECENT valid quote <= each stored member
  day (per-day pointer over the run's valid rows). Regression test:
  tests/test_db.py::test_pit_history_profile_is_latest_quote_not_run_start.
- REBUILT: `data-manager build-universe-pit --history` (13,459,809 stock-days,
  7,187 days) + optimize-db (backup ~/.prime/agent/data_manager.pre-pitfix.db,
  quick_check ok, VACUUM 140s, 15.94GB). Verify: AAPL 2026-08-11 now price
  $304.91 / mcap $4,469B; 1998-01-14 still $19.75.
- Old backups data_manager.pre-optimize.db (17.1GB) and pre-pitfix.db contain the
  BUGGY universe_pit; keep only if the bad profiles are ever needed.

## Reporting v2 (2026-08-12)

- report/data.qmd rewritten: raw-vs-derived lineage tables (with rebuild map and
  the prices.adjustment contract), full SF1 reporting-dimension reference
  (AR=As-Reported, MR=Most-Recent-Reported; Y/Q/T) + 112-column indicator
  dictionary (vendor names, grouped by family), PIT universe analysis (mcap bins,
  threshold sensitivity, sector/industry coverage, per-year median mcap), tear
  sheets for mega/mid/small (picked dynamically), GICS gap, freshness/quality.
- Palette retired navy/gold -> graphite ink + burnt-orange + sage + slate
  (custom.scss $primary #c8502d; report.mplstyle).
