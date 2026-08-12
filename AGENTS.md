# AGENTS.md — data-manager (agent guide)

Operational guide for any agent (or engineer) that reads from, or maintains,
this database. Human-oriented narrative lives in `NOTES.md` and in
`report/data.qmd` (rendered to `report/data.html`). This file is the
standalone, facts-only contract.

## What this is

`~/.prime/agent/data_manager.db` — a single SQLite (WAL) file, the
point-in-time market-data warehouse for the quant stack:
61.7M as-traded daily price rows (stocks + funds, 1998 → now,
survivorship-free), a 3.15M-row SF1 fundamentals mirror (6 reporting
dimensions), the full Sharadar securities master (31,742 instruments, 19,227
delisted), corporate actions (672k rows), metrics, S&P500 membership history,
and the locally built PIT universe (`universe_pit`, 13.46M stock-days over
7,187 trading days).

Raw tables come from Sharadar **bulk-download zips** (full history, no
per-ticker API calls); derived tables are rebuilt locally with zero requests.
**This repo is independent: never assume or reference other projects'
conventions here; names like the factor model in risk-model are separate
projects and must not be mentioned in this repo's docs/code.**

## Connection patterns

In-repo code (jobs/builds):
```python
from data_manager import db
conn = db.connect()            # WAL, busy_timeout, tuned pragmas, schema ensured
```
Downstream read-only consumers (reports, notebooks, agents):
```python
import sqlite3, pathlib
db_path = pathlib.Path.home() / ".prime" / "agent" / "data_manager.db"
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
conn.execute("PRAGMA cache_size=-65536")     # 256MB page cache
conn.execute("PRAGMA mmap_size=134217728")   # 128MB mmap
conn.execute("PRAGMA busy_timeout=20000")
```
Rules: **never write from a downstream consumer**; WAL allows concurrent
readers. Dates are TEXT `YYYY-MM-DD` everywhere — lexicographic comparison is
date comparison. Keys: `prices(ticker,date)`, `sf1(ticker,dimension,
reportperiod)`, `universe_pit(as_of,ticker)`, `securities_master(
permaticker)` — **join master on `ticker`, not `permaticker`**.

## Tables

| table | origin | key | contents |
|---|---|---|---|
| prices | raw (stocks+funds zips) | (ticker,date) | as-traded OHLCV + `adjustment` |
| sf1 | raw (fundamentals zip) | (ticker,dimension,reportperiod) | all 112 vendor fields as native columns (7 TEXT + 105 REAL) — no blob |
| securities_master | raw (tickers zip) | permaticker | instrument roster; `table`=stocks/funds; sector/industry (Sharadar taxonomy) |
| corporate_actions | raw (actions zip) | (ticker,date,action) | splits, dividends, delisted, tickerchanges, bankruptcyliquidation |
| metrics | raw (metrics zip) | (ticker,as_of) | snapshot stats (betas, MA, 52w, returns, div yields) |
| sp500_membership | raw (API) | (ticker,date) | S&P membership history 1957→now |
| french_factors | raw (Ken French Data Library) | date | daily US factor returns in % (percent/day): Mkt-RF, SMB, HML, RMW, CMA, Mom, ST_Rev, LT_Rev, RF |
| fundamentals | derived (sf1 ARY) | (ticker,fiscal_year) | Piotroski F-Score + 9 components |
| quarterly_statements | derived (sf1 ARQ) | (ticker,period) | 13-col quarterly mirror |
| ratios | derived (sf1 MRY latest) | ticker | valuation/quality snapshot (forward_pe & beta always NULL — SF1 lacks them) |
| classifications | derived (GICS map) | ticker | 11 GICS labels — covers only 2,589 iShares-era names (known gap) |
| universe | derived (iShares IWV) | ticker | legacy R3000 snapshot (superseded by universe_pit) |
| universe_pit | derived (master+prices+sf1) | (as_of,ticker) | investable membership per trading day |
| snapshots | ledger | id | per-pull record (source, pulled_at, as_of, row_count) |
| descriptions | raw (bulk) | (table_name, indicator) | Sharadar's field dictionary: title + sentence-level definition + unit type for every column of every vendor table (373 rows, 17 datasets) |

## The data dictionary is IN the database — read `descriptions`

Never guess what a column means. Every column definition is stored in the
`descriptions` table (Sharadar's own field dictionary, 373 rows / 17 vendor
datasets, refreshed by `bulk_update`):

```sql
SELECT table_name, indicator, title, description, unittype
FROM descriptions
WHERE table_name = 'SF1' AND indicator = 'revenue';
```

- Covers all 8 raw warehouse tables: `securities_master` (TICKERS),
  `corporate_actions` (ACTIONS), `sp500_membership` (SP500), `metrics`
  (METRICS), `sf1` (SF1 — all 112 fields stored as native columns), `prices` (SEP/SFP bulk price columns), plus the vendor's
  meta catalogs `TABLE-DESCRIPTIONS` and `ACTIONTYPES`; `french_factors`
  (Ken French Data Library) adds its own dictionary rows via its loader.
- The 7 derived/ledger tables (`fundamentals`, `quarterly_statements`,
  `ratios`, `universe_pit`, `classifications`, `universe`, `snapshots`)
  are NOT vendor fields — their definitions are written by this repo in
  `docs/data_dictionary.md` / `docs/data_dictionary.json` (generated from
  this table; keep them in sync when the schema or semantics change).
  `french_factors` is a raw table from a different vendor, so its column
  definitions are also written as `descriptions` rows (table_name
  `french_factors`) by the factor loader itself.
- If a column has no `descriptions` row, its definition is under "this
  repo (local semantics)" in `docs/data_dictionary.md` — check there
  before inventing semantics.

## securities_master & corporate_actions quick reference

* `securities_master`: one row per instrument (31,742; stocks 21,960 /
  funds 9,782). PK = `permaticker`; `ticker` is the CURRENT/LAST symbol
  (Lehman = LEHMQ even for its NYSE era; historical symbols live only in
  `corporate_actions.tickerchangeto/from`). `isdelisted` Y for 19,227 (==
  the 19,227 `delisted` action rows, 1:1). `sector/industry` is the
  Sharadar taxonomy (~99.4% stock coverage) — NOT GICS (that's
  `classifications`, 2,589 names only). `firstpricedate/lastpricedate`
  bound `prices`; `lastpricedate` = delisting date for dead names.
* `corporate_actions` (670,752): one row per event, key (ticker,date,
  action). `value` units are action-specific and verified for: split
  (ratio s in s:1 — feeds the adjustment chain), dividend ($/share,
  date = ex-date), spinoff (new shares per original share). Paired events
  use `contraticker/contraname` (tickerchange, acquisition, relation,
  spinoff sides); N/A otherwise. For `delisted`/`acquisitionof`,
  `value` is a vendor context number, NOT the last trade price (use
  `prices` / `master.lastpricedate`). `initiated` rows are index/benchmark
  first-appearance (^VIX etc.). Report §3 documents both tables with live
  samples.

## Prices: the adjustment contract (critical)

* `close` is the **as-traded** price (it gaps at stock splits).
* `adjustment = closeadj / close(t)` where `closeadj` is the vendor's
  total-return-adjusted close, normalized so `adjustment = 1.0` on the
  latest quote. Apply `adjusted = raw × adjustment` for any OHLC field
  (volume stays as traded).
* **Construction (verified 2026-08-12):** adjustment is a *cumulative,
  multiplicative* chain — one factor per corporate action after the day
  (split `s:1` → `1/s`; cash dividend `d` on ex-date → `≈ 1 - d/c`), all
  multiplied together. The single stored number IS the whole collapsed
  product (it telescopes): rebuilding the chain from `corporate_actions` +
  `prices` reproduces the stored column to vendor precision (~1e-4 rel on
  old rows, ~1e-6 recent — their decimal rounding).
* **Meaning:** `adjusted(t2)/adjusted(t1) - 1` = buy-and-hold total return
  with dividends reinvested (multiplicatively at the ex-date price — a
  convention, not literal reinvestment). The adjusted series is NOT a
  tradable price and it embeds FUTURE events (normalized to end of
  history): for point-in-time work use as-traded `close` and rebuild
  adjustments only from events known by date D, or you import look-ahead.
* **Screens when using `adjustment`:** keep `adjustment BETWEEN 0.01 AND
  100` (18,073 rows carry the 8.5e18 vendor sentinel; ~1.4M junk-factor
  rows total, mostly zero-volume days); also drop `volume = 0` (5.5% of
  rows) for return work.
* **PIT/look-ahead rule (verified identity):** stored `adjustment(t)` =
  product of factors of ALL events after t (anchored to 1.0 at the latest
  quote). For a decision date D: `adjustment(t) = A(t;D) * adjustment(D)`
  where A(t;D) is the factor chain using only events known by D — the
  correction is ONE division, not a rebuild. RETURNS (adj-close ratios)
  are already PIT-safe (future factors cancel); LEVELS as of D must be
  rebased: `close(t) * adjustment(t) / adjustment(D)` — 
  `adjusted_prices(..., asof=D)` in this repo does exactly that (tests:
  `test_adjusted_prices_rebases_to_asof`). Absolute $ thresholds or
  cross-stock comparisons on adjusted LEVELS are never valid — use
  as-traded close (universe_pit membership already does).
* Convenience accessor: `adjusted_prices(ticker, start=None, end=None)` in
  `data_manager.universe` → dicts with `date/open/high/low/close/volume/
  adjustment/adjusted_open.../adjusted_close`.
* Inner joins vs master: `securities_master.table` splits stocks (21,960)
  vs funds (9,782); funds are reference series only.

## SF1: dimensions, dates, and the full column set

* Dimensions: `AR*` = **As-Reported** (no restatements; `date` ≈ SEC filing
  date → use `date <= D` for point-in-time), `MR*` = **Most-Recent
  Reported** (includes restatements, indexed to report period). Suffix
  `Y` annual, `Q` quarterly, `T` TTM. All six dims exist on the bulk file
  (ART/MRT too — the API serves none).
* Row fields: `date` = date key (as-of; vendor name `datekey`); `calendardate`
  = period calendar date; `reportperiod` = fiscal period end; `fiscalperiod`
  = e.g. `2025-FY`; `lastupdated` = vendor refresh timestamp.
* **ALL 112 vendor fields are native columns** (7 key/date TEXT + 105 REAL
  indicators; no blob, no decompression — since the 2026-08-12 migration).
  Any indicator is SQL-queryable: `SELECT revenue, rnd, opex FROM sf1
  WHERE ticker=? AND dimension='ARY' AND date <= ?`. The column list is
  `db.SF1_INDICATORS` (also docs/data_dictionary.md). `_hydrate_sf1(conn,
  ticker, dimension)` returns full rows as vendor-keyed dicts (surfacing
  `date` as `datekey` for caller compatibility). Do NOT re-introduce a
  blob/lite-column split — this is a storage/query project: serve the
  vendor schema as-is.
* PIT market cap: `close × shareswa` (ARQ/ARY ≤ date). Banks' `grossmargin`
  ≈ 1.0 and `fcf` structurally negative — expected artifacts.

## French factor returns (`french_factors`)

One row per US trading day, `date` PRIMARY KEY (TEXT `YYYY-MM-DD`; the
source's `YYYYMMDD` converted), covering **1926-01-26 → present** (26,403
rows). Raw from the **Ken French Data Library** daily factor files
(mba.tuck.dartmouth.edu/ftp, five CSV zips, free; the only non-Sharadar
raw table in the warehouse):

| column | source file | starts |
|---|---|---|
| `mkt_rf`, `smb`, `hml`, `rf` | Fama/French 3 Factors | 1926-07-01 |
| `rmw`, `cma` | Fama/French 5 Factors (2x3) | 1963-07-01 |
| `mom` | Momentum Factor | 1926-11-03 |
| `st_rev` | Short-Term Reversal | 1926-01-26 |
| `lt_rev` | Long-Term Reversal | 1930-03-20 |

- **Units are PERCENT per day** (0.09 = 0.09%), exactly as the site serves
  them. The vendor missing sentinel `-99.99` is stored **NULL**.
- `rf` is the daily risk-free rate (the simple daily rate that compounds to
  the 1-month T-bill over the month's trading days). It is served by both
  the 3F and 5F files with identical values (verified on all 15,854
  overlapping dates) — stored once.
- The factors are value-weighted **long-short spreads** built from the
  site's 6-portfolio buckets (e.g. `smb = 1/3(Small V+N+G) − 1/3(Big
  V+N+G)`); they are *market* factor series, not tradable instruments —
  never join them to `prices` by ticker. Column definitions live in the
  `descriptions` table (`table_name='french_factors'`, written by the
  factor loader along with the data).
- Refresh: `uv run data-manager update-french-factors` (manifest-skipped
  via Last-Modified; `--force` re-downloads). `bulk-update`/`bulk-fromzero`
  refresh it automatically; French loads never trigger the SF1
  derivations/PIT.

## universe_pit semantics (read this twice)

One row per (trading day, member). Membership iff, **as of that day**:
Domestic Common Stock (Primary/Second class), as-traded close ≥ $2, trailing
20-session avg $volume ≥ $5M (≥10 sessions), PIT mcap ≥ $300M, quote ≤ 10
calendar days old. The stored `price/mcap/dvol_avg/dvol_days` is the **most
recent valid quote ≤ that day** (fixed 2026-08-12 — a continuous name must
carry today's profile, not its 1998 one; regression-tested). Index:
`idx_pit_asof` — always filter by `as_of` first; do not aggregate thousands
of members across all days without the as_of predicate.

## Gotchas that bite

1. `securities_master` PK is **permaticker**; ticker is unique in practice.
2. Master `ticker` is the **last/current** symbol (Lehman is `LEHMQ` even
   for its NYSE era; the historical `LEH` symbol is only in
   `corporate_actions` ticker-change rows).
3. `classifications` (GICS) covers only the 2,589 iShares-era names —
   master `sector/industry` (Sharadar taxonomy, e.g. "Healthcare") is the
   full-coverage alternative and must not be labeled GICS.
4. `metrics` is snapshots-only and uneven (dead stocks have one row at
   delisting; some fields are artifacts — verify before use).
5. `ratios.forward_pe` and `beta` are always NULL (SF1 does not provide).
6. 2 price rows have `close ≤ 0`; ~5.5% have `volume = 0`; screen both.
7. `bulk_update` reloads full-history zips with INSERT OR REPLACE — do not
   run writers concurrently with it; keep read-only connections.
8. Report rendering: `quarto render report/data.qmd` from the repo root
   (jupyter kernel `data-manager` = this repo's venv). Never put `eqrm` or
   other projects' names in docs/reports/code here.

## Keeping the database updated (hygiene)

1. `uv run data-manager status` — baseline row counts (also `snapshots` for
   the pull ledger).
2. `uv run data-manager bulk-update` — syncs each table's zip against the
   server's `modified` stamp (`~/.prime/agent/bulk/_manifest.json`),
   re-downloads only changed files (~2 GB/day worst case), reloads those
   tables (prices full reload ≈ 12 min), re-derives piotroski/quarterly/
   ratios, rebuilds `universe_pit --history` (≈ 12 min), and refreshes
   `french_factors` from the Ken French Data Library (tiny files, own
   Last-Modified skip in the same manifest; a French-site outage never
   aborts the rest). Log: `logs/`.
3. `uv run data-manager optimize-db --backup <path>` — consistent backup,
   checkpoint, ANALYZE, `quick_check`, VACUUM (~3 min). Run after every
   update; keep one backup per full rebuild (DB ≈ 16 GB).
4. Verify after every update:
   * `status` counts vs the previous baseline (no table should shrink).
   * `SELECT MAX(date) FROM prices` and `MAX(as_of) FROM universe_pit`
     moved forward together.
   * Spot check a well-known name (AAPL last close sane; 2026 profile
     ≈ live price, 1998-01-14 ≈ $19.75 — the fixed-profile fingerprint).
   * `uv run pytest -q` (96 tests, incl. the PIT profile regression).
5. Cadence: the bulk zips are daily; a morning `bulk-update` + `optimize-db`
   keeps the warehouse current. `bulk-fromzero` is the full rebuild path
   (downloads everything, wipes, loads, derives, PIT, optimizes).
6. Logs/scripts: `logs/*.log`, `run_pit_history.sh`, `run_optimize.sh`,
   `measure_perf.sh` (query-latency smoke).

## Read paths by task

| task | pattern |
|---|---|
| daily bar / returns | `SELECT date,close,volume,adjustment FROM prices WHERE ticker=? [AND date>=?]` |
| total-return series | as above, compute `close*adjustment` yourself (accessor: `adjusted_prices`) |
| cross-section on date D | `SELECT * FROM prices WHERE date=?` (uses `idx_prices_date`) |
| PIT shares/mcap for (t,D) | `SELECT shareswa FROM sf1 WHERE ticker=? AND dimension IN ('ARQ','ARY') AND date<=? ORDER BY date DESC LIMIT 1` |
| investable members on D | `SELECT * FROM universe_pit WHERE as_of=?` (uses `idx_pit_asof`) |
| fundamentals history | `SELECT * FROM sf1 WHERE ticker=? AND dimension='ARY' ORDER BY reportperiod` (all 112 fields native columns) |
| delisting evidence | `corporate_actions` rows `action IN ('delisted','bankruptcyliquidation')` |
| factor returns (daily) | `SELECT date, mkt_rf, smb, hml, rmw, cma, mom, st_rev, lt_rev, rf FROM french_factors WHERE date >= ?` (returns are %/day) |
| column definition / unit | `SELECT title, description FROM descriptions WHERE table_name=? AND indicator=?` — see the **"data dictionary is IN the database"** section above; derived-table columns: `docs/data_dictionary.md` |
