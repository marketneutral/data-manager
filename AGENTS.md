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
insider transactions (SF2, `insider_transactions`), 13F institutional-ownership
holdings (SF3, `institutional_ownership`), and the locally built PIT universe
(`universe_pit`, 13.46M stock-days over 7,187 trading days).

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
| insider_transactions | raw (insiders zip, SF2) | (ticker,filingdate,formtype,ownername,rownum) | SEC insider Form 3/4/5 filings (owner, role, buy/sell, shares, price, value). Column defs in `descriptions` (table_name `SF2`). `date` stored as `filingdate`; `transactionshares` signed (+buy/−sell) |
| institutional_ownership | raw (sf3 zip, SF3) | (ticker,calendardate,investorname,securitytype) | 13F institutional holdings (investor, quarter-end, security class, units in thousands, value in USD millions). Column defs in `descriptions` (table_name `SF3`). `date` stored as `calendardate`, `investorid` as `investorname` |
| fundamentals | derived (sf1 ARY) | (ticker,fiscal_year) | Piotroski F-Score + 9 components |
| quarterly_statements | derived (sf1 ARQ) | (ticker,period) | 13-col quarterly mirror |
| ratios | derived (sf1 MRY latest) | ticker | valuation/quality snapshot (forward_pe & beta always NULL — SF1 lacks them) |
| classifications | derived (GICS map) | ticker | 11 GICS labels — covers only 2,589 iShares-era names (known gap) |
| universe | derived (iShares IWV) | ticker | legacy R3000 snapshot (superseded by universe_pit) |
| split_events | derived (corporate_actions splits) | (ticker,date) | one row per split: the raw table can hold a split twice after a refresh re-serves it (more decimals, or moved a day); `rule` says how rows were merged |
| shares_pit | derived (sf1 ARQ/ARY + split_events) | (ticker,from_date) | point-in-time shares outstanding in as-traded units: SF1 `sharesbas × sharefactor` (else `shareswa`) ÷ splits after the date; a row holds until the next `from_date` |
| universe_pit | derived (master+prices+shares_pit) | (as_of,ticker) | investable membership per trading day |
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
* `corporate_actions` (740,770 after the 2026-10-26 refresh): one row per vendor row, key (ticker,date,
  action,value). A refresh that re-serves a split at more decimals (0.33333 →
  0.333333333) or a day later adds a row beside the old one, so **never multiply raw
  split rows; use `split_events`** (one row per split). `value` units are action-specific and verified for: split
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
* **PIT market cap: `prices.close × shares_pit.shares`** (the `shares_pit` row with the
  latest `from_date ≤ D`). **Never `close × shareswa`:** SF1 share counts (`shareswa`,
  `sharesbas`, ...) are restated by the vendor for every split *after* the filing, exactly like
  SF1 `price` (AAPL's 2014 filings carry ~24.5B shares, ~28× the count then outstanding, after
  7:1 in 2014 and 4:1 in 2020). Multiplying them by an as-traded close overstates market cap by
  every later split and leaks future splits (stocks split after they rise). `shares_pit` divides
  each filing's count by the splits after the date; it changes at every filing and every split
  ex-date. The count is **`sharesbas × sharefactor`** (period-end basic shares × the ADR factor,
  the vendor's own `marketcap` basis), falling back to `shareswa` only where `sharesbas` is
  missing (`share_field` says which): the weighted average lags issuance (off by >5% for ~8% of
  filings). Restated counts remain correct for comparisons between periods of the same stock
  (they share one basis), e.g. the F-Score's equity-issuance test.
  Verified 2026-10-25 on the full history: `close × shareswa` fell by the split ratio on 94% of
  2,716 split days and matched SF1 `marketcap` within 5% at only 75% of ARQ report dates;
  `close × shares_pit` is flat across split days and matches SF1 `marketcap` within 1% at 99.9%.
* **The same restatement applies to every SF1 per-share and share-count field** (`price`,
  `eps`, `epsdil`, `bvps`, `dps`, `fcfps`, `sps`, `tbvps`, `shareswa`, `shareswadil`,
  `sharesbas`): ratios between two of them, or with `marketcap`, are split-invariant, but their
  *levels* are in the latest share units. Ranking stocks on a level imports future splits; to
  put a level on the as-of basis, multiply per-share fields by (shares divide by) the
  `split_factor` of the matching `shares_pit` row.
* **DO NOT use the SF1 `price` column as an as-traded price / factor.** It is **split-adjusted to forward time**: adjusted for splits/reverse-splits that occur *after* the point-in-time date (e.g. AAPL SF1`price`=106.26 for a period whose genuine traded price was ~$425, i.e. ÷4:1 split that came later; reverse-split penny stocks are distorted by factors 10^9-10^17). Using it to sort/rank cross-sectionally leaks future info (look-ahead) and mis-ranks stocks on true 'cheapness'. For a genuine price-level factor use `prices.close` (as-traded) at the rebalance date; keep `close × adjustment` for *returns* only. Do NOT drop the column (keep the 112-field vendor schema) — just stop using it.
 Banks' `grossmargin`
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

## SF2: insider transactions (`insider_transactions`) semantics

One row per SEC insider filing line, 2008-01-02 → present (11.5M rows, 12k
tickers). Key = (ticker, filingdate, formtype, ownername, rownum). Raw from the
Sharadar `insiders.csv.zip` bulk file.

* **`filingdate`** = the SEC filing date (source column `date`); **`transactiondate`**
  is the actual trade date (≈ the event). File date lags trade date (median 2 days,
  tail to years) — a *look-ahead-free* point-in-time signal must filter on
  `filingdate <= decision date`, NOT `transactiondate` (see "SF2 PIT & splits" below).
  Canonical `formtype` = `3` (initial), `4` (changes), `5` (annual); prefixed
  `RESTATED ` for re-filings.
* **`transactionshares` is signed**: positive = acquisition, negative = disposition
  (e.g. `-1439` = sold 1,439). `transactioncode` is the SEC code — `P` open-market
  purchase, `S` open-market sale, plus `A/D/M/X/C/F/G/J/V/W` (grants, gifts, expiry,
  derivative exercises...). `securityadcode` (`NA/ND/DA/DD/N/D`) splits
  non-derivative vs derivative and acquisition vs disposition.
* **`transactionvalue`** is the trade value **in USD** (e.g. 442478 = \$442,478) for
  most rows, despite the dictionary's "millions" wording — treat it as raw dollars and
  verify before dividing. `sharesownedbefore/followingtransaction` are the owner's
  recorded holdings around the trade (often blank on old rows).
* **Roles**: `officertitle` (semi-colon role list), `isdirector/isofficer/
  istenpercentowner` (Y/N), `directorindirect` (D/I). **Never assume a blank
  `directorindirect` = direct** — check `natureofownership` (trust/fund vehicle)
  before flagging indirect ownership.
* Duplicate protection: `INSERT OR REPLACE` on the full key, so re-running a load
  (e.g. a daily `--tables insiders`) is idempotent. `descriptions` (table_name
  `SF2`) holds the authoritative column dictionary.

### SF2 PIT & splits (verified empirically 2026-08-22)

**SF2 is point-in-time clean: price, share counts, and value are all in the
as-traded frame at the transaction date — NOT split-adjusted backwards.**
Verified on names with later forward splits: every pre-split trade reports the
*as-traded* price and *as-traded* share counts that day (AAPL 2011 ≈ $340 not
~$12 after 7:1(2014)×4:1(2020); AMZN ≈ $189 not $9.5 after 20:1; NVDA ≈ $22 not
$2.2 after 10:1; GOOGL ≈ $604 not $30 after 20:1; TSLA ≈ $23 not $1.5 after
5:1/3:1), and on every sampled pre-split row `transactionpricepershare ×
transactionshares == transactionvalue` exactly, so the **share counts are
as-traded too**, not post-split. P/S price/close ≈ 1.0 confirms the price equals
the as-traded close on the trade date. Do **not** mix these share counts with the
SF1 back-adjusted `price` column (which IS adjusted forward and leaks future
splits) — use `prices.close` (as-traded) at the decision date for any
$-denominated magnitude.

**PIT timing:** a trade is knowable to you only once it is **filed**; filter
`filingdate <= decision_date` (not `transactiondate`) to avoid look-ahead. Median
trade→filing lag 2 days (63% ≤2d, p90 4d, p99 115d), tail to years. `RESTATED`
(272k rows) are re-filings that get separate key rows — dedup on `filingdate` is
naive; take latest per (ticker, transactiondate, ownername, rownum,
formtype-base). `ticker` is the current/last symbol (match `securities_master`
on `ticker`). `transactionvalue` is USD.

## SF3: 13F institutional ownership (`institutional_ownership`) semantics

One row per (ticker, quarter-end, investor, security type) — 13F institutional
holdings, live now (the full bulk file is ~81M rows). Source = Sharadar `sf3`
bulk zip (`holdings.csv.zip`). Where the vendor servies duplicate rows for the
same (ticker, quarter, investor, class), the loader collapses on the key.

* **Calendar cadence**: `calendardate` is the **last day of the calendar
  quarter** (vendor column `date`) — 13F filings are quarterly (Mar/Jun/Sep/Dec
  31). This is the reporting quarter-end, **not** the filing date (13F filings
  lag by up to ~45 days), so for a point-in-time signal treat the holding as
  known only once the quarter has ended **and** the form is filed.
* **VERIFIED (2026-08-25):** the vendor does **NOT** add the filing lag. Sharadar's
  data dictionary describes `calendardate` as "the last day of the calendar
  quarter" (pure period date). EDGAR spot-check (Berkshire 13F-HR): the
  2023-12-31 quarter-end holdings were **filed 2024-02-14 (45 days later)**; the
  stored AAPL 2023-12-31 SHR row (905,560 units = 905.56M sh, value 174,347.5)
  matches EDGAR's infotable **exactly**. So treat holdings as knowable only at
  quarter-end + ~45d; do **not** add a second lag.
* **`investorname`** is the vendor's sticky 6-char institutional investor code
  (vendor column `investorid`; e.g. `MARKOW`, `WASHI2`). It is NOT a readable
  name — join/lookup investor identity elsewhere if names are needed.
* **`securitytype`**: `SHR` Common Shares, `FND` Fund Units, `CLL` Call
  Options, `PUT` Put Options, `WNT` Warrants, `DBT` Debt, `PRF` Pref, ... —
  filter to `SHR` for common-equity ownership work.
* **Units vs Value**: `units` is in **thousands of units** (integer); `value`
  is in **USD millions** (1 decimal). So shares ≈ `units × 1000`, USD ≈
  `value × 1e6`. Do not mix the two scales.
* Duplicate protection: `INSERT OR REPLACE` on `(ticker, calendardate,
  investorname, securitytype)`; re-running a load (e.g. `--tables sf3`) is
  idempotent. `descriptions` (table_name `SF3`) holds the authoritative column
  dictionary. Aggregating owner count / ownership concentration across the
  whole ~81M-row table requires the `idx_instown_caldate` / `idx_instown_ticker`
  predicates (always filter by `calendardate` first).

## universe_pit semantics (read this twice)

One row per (trading day, member). Membership iff, **as of that day**:
Domestic Common Stock (Primary/Second class), as-traded close ≥ $2, trailing
20-session avg $volume ≥ $5M (≥10 sessions), PIT mcap (`close × shares_pit`,
see SF1 above) ≥ $300M, quote ≤ 10 calendar days old. A rebuild deletes every
stored row in its window first, so a (day, name) that no longer qualifies does
not survive (before 2026-10-25 a rebuild only upserted, so stale members could
persist; and mcap used restated shares, which put names that later split into
the universe early and kept reverse-split names out). The stored `price/mcap/dvol_avg/dvol_days` is the **most
recent valid quote ≤ that day** (fixed 2026-08-12 — a continuous name must
carry today's profile, not its 1998 one; regression-tested). Index:
`idx_pit_asof` — always filter by `as_of` first; do not aggregate thousands
of members across all days without the as_of predicate.

## Point-in-time contract (read before any backtest)

A value is point-in-time only if it was **known on the decision date, in that day's units**.
For each table: the column that says when a row became known, what its levels are in, what is
safe, and the remedy for what is not. Audited 2026-10-25 against EDGAR and the vendor's own
numbers; `data-manager pit-audit` re-checks the invariants marked ✓ after every bulk update.
Prefer the accessors in `data_manager.pit` (`pit_members`, `pit_market_cap`, `pit_fundamentals`,
`pit_shares`, `split_factor_after`) to hand-written joins: they take any calendar date and apply a
fact from its knowledge date onward.

| table | known on | levels are in | safe | not safe → remedy |
|---|---|---|---|---|
| `prices.close/open/high/low/volume` | `date` (as-traded) | that day's units | levels, $ thresholds, dollar volume | — |
| `prices.adjustment` | `date` | latest units (folds in every later split/dividend) | return ratios | levels as of D → rebase `adj(t)/adj(D)` (§Prices) |
| `sf1` ARQ/ARY/ART | `date` = SEC filing date (98.8% exactly EDGAR's; 0.09% earlier) | **dollar totals: as filed. Per-share fields and share counts: latest share units** | dollar totals; ratios between fields; `marketcap`, `pe`, `pb`, `ps` (vendor-computed at `date`) | per-share/share-count **levels** (`price`, `eps`, `epsdil`, `epsusd`, `bvps`, `dps`, `sps`, `fcfps`, `tbvps`, `sharesbas`, `shareswa`, `shareswadil`) → `pit_fundamentals(split_basis=True)` |
| `sf1` AR values | | the **original** filing's (99.9%; 99.3% of later-restated periods keep the original, 0.0% the restatement) | as-of use with `date <= D` | — |
| `sf1` MRQ/MRY/MRT | indexed to the report period | **restated after the fact** | research on final numbers only | any backtest → use AR* (the accessor refuses MR* unless `allow_restated=True`) |
| `sf1` `roa`, `roe`, `ros`, `roic`, averages | | empty in ARQ | use the ART/ARY row of the same filing | — |
| `split_events` ✓ | `date` = ex-date | | **every split ratio product** (split factors, share basis) | multiplying raw `corporate_actions` split rows → a re-served split is counted twice (1,572 after the 2026-10-26 refresh) |
| `shares_pit` ✓ | `from_date` (filing date or split ex-date) | that day's units | `close × shares` = PIT market cap | — |
| `universe_pit` ✓ | `as_of` | profile is that day's quote | membership and profile on `as_of` | `sector`/`industry` are the **latest** classification on every day (no history exists) |
| `fundamentals`, `quarterly_statements` ✓ | `filing_date` (added 2026-10-25) | as filed (`shares_out` mirrors restated `shareswa`) | as-of on `filing_date` | a fixed lag after period end (2.1% of annual filings come out >180 days after Dec 31) → use `filing_date` |
| `ratios`, `metrics` | snapshot date | latest | current screens | **never history** — snapshots, not point-in-time |
| `securities_master` | current | latest | identity, current labels | historical classification → none available |
| `corporate_actions` | `date` = effective/completion date (split ex-date; acquisition/delisting completion: TWTR 2022-10-27, announced in April; LEHMQ bankruptcy dated its 2008-10-15 delisting) | | splits (ex-date known in advance), events as of completion | an event *signal* at announcement → not in this table |
| `sp500_membership` | `date` = effective date (TSLA 2020-12-21, announced 2020-11-16) | | membership as of D | index-change announcement signals → not in this table |
| `insider_transactions` (SF2) | `filingdate` | as-traded | filter `filingdate <= D` | `transactiondate` alone → look-ahead |
| `institutional_ownership` (SF3) | `calendardate` = quarter end; filed ~45 days later | | `calendardate + 45d <= D` | quarter-end dating → look-ahead |
| `french_factors` | `date` | — | returns | the Ken French library revises history between releases (no vintages kept) |

**Joins.** An as-of join must carry a fact from its knowledge date to every later date, including
when the knowledge date is not a trading day. Reindexing a sparse series straight onto trading
dates silently drops every weekend-dated fact (52/53-week filers' Saturday period ends + a lag land
on Sundays; Dec 31 + 180 days is a weekend in some years).

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
8. `insiders` (SF2) and `sf3` (SF3) are part of the default `bulk-update` set
   but never trigger the piotroski/quarterly/ratios/PIT rebuilds (they feed
   nothing); refresh either alone, e.g. `--tables sf3`.
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
   * `uv run pytest -q` (127 tests, incl. the PIT profile, PIT-shares, split-events and pit-audit regressions).
   * `uv run data-manager pit-audit` must report no `error` (bulk-update runs it and fails on
     one); results accumulate in `pit_audit_runs`. See "Point-in-time contract".
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
| insider buys/sells (PIT) | `SELECT * FROM insider_transactions WHERE ticker=? AND transactioncode IN ('P','S') AND filingdate<=?` — use **filingdate** (knowable by then), signed `transactionshares`, as-traded `transactionpricepershare` |
| 13F holders of a name | `SELECT investorname, securitytype, units, value FROM institutional_ownership WHERE ticker=? AND securitytype='SHR' AND calendardate=?` (units in thousands, value in USD millions) |
| factor returns (daily) | `SELECT date, mkt_rf, smb, hml, rmw, cma, mom, st_rev, lt_rev, rf FROM french_factors WHERE date >= ?` (returns are %/day) |
| column definition / unit | `SELECT title, description FROM descriptions WHERE table_name=? AND indicator=?` — see the **"data dictionary is IN the database"** section above; derived-table columns: `docs/data_dictionary.md` |
