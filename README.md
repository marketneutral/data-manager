# data-manager

Acquisition, storage, and point-in-time market data for the quant research stack.

- **Universe:** Russell 3000 (~2,600 tickers) from the free iShares IWV holdings CSV.
- **Prices:** daily **as-of (as-traded) OHLCV + an adjustment factor** — Sharadar
  `stocks` + `funds` tables (ETFs/ETNs/CEFs), survivorship-bias-free, back to 1998
  (and per-security inception where earlier), including **delisted names**.
- **Fundamentals:** annual **Piotroski F-Score with all 9 components** from
  Sharadar SF1 (as-reported, point-in-time dimension available).
- **Quarterly:** quarterly income/balance/cash-flow statements (SF1).
- **Ratios:** point-in-time valuation/quality ratios (SF1-derived; forward_pe and
  beta are NULL — SF1 does not carry them).
- **Securities master:** full Sharadar `tickers` rows (sector/industry, delisted
  flags, first/last priced dates, FIGI, SIC, CUSIPs, exchange, ...).
- **Corporate actions:** splits, dividends, ticker changes, delistings.
- **S&P500 membership:** current members + per-member history back to the 1980s.
- **Storage:** a single local SQLite database (`~/.prime/agent/data_manager.db`).

**Data provider: Sharadar is the sole provider** (FMP removed 2026-08-11 — fresh
start; `archive/fmp-era/` holds the old provider, docs, logs, and the pre-wipe
database). Everything is resumable, paced, resilient, and commits incrementally.

**ETF extras:** alongside the stock universe, `src/data_manager/etfs.py` maintains a
manifest of tradable proxies (benchmarks SPY/QQQ/IWM/IWV, VXX for vol, the 11 SPDR
sector funds, bond funds AGG/TLT/SHY/IEF/LQD/HYG, international EFA/EEM, GLD/USO,
and the dollar fund UUP). They live in `prices`/`classifications` (sector `ETF`,
industry = category) but NOT in `universe`, so universe-wide jobs (fundamentals,
ratios, quarterly) skip them.

---

## Quick start

```bash
cd ~/dev/data-manager
uv venv && uv pip install -e .        # one-time setup
# SHARADAR_API_KEY in ~/.env  (already there)

uv run data-manager status            # live coverage snapshot
uv run data-manager update-universe   # R3000 from iShares IWV (free, 1 request)
uv run data-manager update-master --all               # securities master (tickers)
uv run data-manager update-actions --all              # corporate actions
uv run data-manager update-prices --all --start 1996-01-01   # as-of OHLCV + adjustment
uv run data-manager update-classifications --all --force     # GICS sector/industry
uv run data-manager update-fundamentals --all --force        # F-score (SF1 AR)
uv run data-manager update-quarterly --all --force           # SF1 quarterly
uv run data-manager update-ratios --all --force              # ratio snapshots
uv run data-manager update-sp500                             # S&P500 membership (current + history)
```

All `update-*` jobs are **resumable** (skip already-covered tickers unless
`--force`), **resilient** (per-ticker errors logged, not fatal), **paced** (0.2-0.5 s
between tickers), and **commit incrementally** (progress visible in `status`).

---

## Architecture

```
providers/            data sources (pluggable)
  ishares.py            IWV holdings CSV  -> universe + sector (free)
  sharadar.py           THE provider: prices (stocks+funds), SF1 fundamentals,
                        quarterlies, ratios, securities master, corporate
                        actions, sp500 membership, classifications (GICS-mapped)
  financialdatasets.py  legacy paid alternative (kept, not default)
universe.py           orchestration: update_* functions (resumable, paced, resilient)
enrich.py             optional SEC/OpenFigi enrichment (FIGI/CIK/SIC/LEI) — legacy;
                      superseded for most fields by the Sharadar securities master
db.py                 SQLite schema + connection (WAL, busy_timeout)
cli.py                `data-manager` command line
final_verify.py       end-of-pipeline assertions
```

**Provider semantics (critical, verified 2026-08-11):** Sharadar `open/high/low/close`
are **split-adjusted**; `closeunadj` is the as-traded close. `providers/sharadar.py`
recovers as-traded OHLCV (factor = closeunadj/close) and stores `adjustment =
closeadj/closeunadj`, so `close × adjustment → closeadj` — the same semantics FMP
used (`adjClose / as-traded close`). Validated against Yahoo on the 10 largest
VXX/FMP discrepancies: Sharadar matched Yahoo exactly (0.00% off), FMP was 2-6% off.

---

## Database schema (`~/.prime/agent/data_manager.db`)

### `universe` — Russell 3000 membership (PK: ticker)
ticker, name, source (IWV), added_at + optional legacy enrichment columns
(figi/cik/sic/sic_description/lei — see `enrich.py`).

### `securities_master` — full Sharadar tickers rows (PK: permaticker)
permaticker, ticker, name, exchange, isdelisted, category, cusips, siccode,
sicsector, sicindustry, figi, famaindustry, sector, industry, scalemarketcap,
scalerevenue, relatedtickers, currency, location, firstadded, firstpricedate,
lastpricedate, firstquarter, lastquarter, secfilings, companysite, lastupdated.

### `corporate_actions` — Sharadar actions (splits/dividends/delistings)
ticker, date, action, name, value, contraticker, contraname.

### `sp500_membership` — S&P500 (PK: ticker, date)
ticker, date, action (`current|historical|added|removed`), name, contraticker,
contraname, note. The unscoped endpoint serves ~1 year; per-ticker pulls give
full history (e.g. AAPL back to 1982-11-30).

### `prices` — as-of OHLCV + adjustment (PK: ticker, date)
date, open, high, low, close, volume — **raw as-traded** prices; `adjustment` =
per-day factor (splits + dividends). **Adjust at query time:**
`adjusted_prices(ticker, start, end)` returns `adjusted_open/high/low/close =
raw × adjustment`. Volume stays as traded.

### `classifications` (PK: ticker)
sector (mapped to 11 GICS labels; unmapped -> NULL = fails closed for the eqrm
inclusion filter), industry, as_of.

### `fundamentals` — Piotroski F-Score (PK: ticker, fiscal_year)
roa, cfo, d_roa, accruals, d_leverage, d_liquidity, equity_issuance,
d_gross_margin, d_asset_turnover (the 9 signals, 0/1) + f_score (0-9).
Components stored, not just the score — rebuild/re-weight or use signals as features.

### `ratios` — point-in-time snapshot (PK: ticker, as_of)
trailing_pe, forward_pe (NULL from SF1), price_to_book, price_to_sales, roe, roa,
net_margin, gross_margin, operating_margin, debt_to_equity, current_ratio,
dividend_yield, market_cap, enterprise_value, ev_to_ebitda, beta (NULL from SF1),
shares_outstanding. Units: margins/ROE as fractions (0.34 = 34%), dividend_yield
raw fraction (0.0042 = 0.42%).

### `quarterly_statements` — SF1 quarterly (PK: ticker, period)
net_income, revenue, gross_profit, operating_cash_flow, total_assets,
total_liabilities, current_assets, current_liabilities, shares_out, roa, cfo.

### `snapshots`
Provenance: source, pulled_at, as_of, row_count for each universe pull.

---

## CLI reference

```
data-manager status                     live counts
data-manager update-universe            R3000 from iShares IWV (free)
data-manager update-master [--all|--ticker A,B]        securities master
data-manager update-actions [--all|--ticker A,B]       corporate actions
data-manager update-prices --all|--ticker A,B --start YYYY-MM-DD [--end] [--force]
data-manager update-classifications --all [--force] [--ticker A,B]
data-manager update-fundamentals --all [--force] [--ticker A,B]
data-manager update-quarterly --all [--force] [--ticker A,B]
data-manager update-ratios --all [--force] [--ticker A,B]
data-manager update-sp500                              S&P500 membership
data-manager enrich-cik | enrich-figi | enrich-sic     legacy SEC/OpenFigi enrichment
```

---

## Data sources

| Source | Used for | Cost |
|---|---|---|
| iShares IWV CSV | universe + sector | free |
| **Sharadar** | prices (stocks+funds), SF1 fundamentals/quarterlies/ratios, master, actions, sp500, classifications | paid subscription |
| SEC / OpenFigi | legacy enrichment (figi/cik/sic/lei) | free |

---

## Query-time adjustment example

```python
from data_manager import adjusted_prices
rows = adjusted_prices("AAPL", "2020-03-16", "2020-03-20")
for r in rows:
    print(r["date"], r["close"], "->", r["adjusted_close"])
```

---

## Bulk downloads (Sharadar's sanctioned path for large extracts)

Sharadar pre-generates whole-table zips (`api.sharadar.com/v1.0/data/<table>?
api_key=...&years=full` redirects to a time-limited URL; see
sharadar.com/docs/bulk). Two orchestration commands cover rebuilds and daily
updates; a JSON manifest next to the zips (`~/.prime/agent/bulk/_manifest.json`)
tracks what was last loaded so `bulk-update` re-downloads only tables the server
regenerated:

```
uv run data-manager bulk-download all [--status] [--force]   # fetch zips only
uv run data-manager bulk-fromzero    [--dir D] [--asof D]    # download all, wipe,
                                    # load all, derive, build PIT  (full rebuild)
uv run data-manager bulk-update      [--tables a,b] [--force]  # manifest-skipped
                                    # re-download, reload, re-derive, PIT
```

Tables: tickers, stocks (46.3M rows full, 1998->), funds, actions (673k),
metrics (with history to 1997), sp500, fundamentals (6 dims incl. TTM ART/MRT,
1990->). Bulk semantics verified: price volume is as-traded; OHLC split-adjusted
(closeunadj = as-traded close); SF1 report date is `datekey`.

## Verification

- `uv run pytest tests/ -q` — unit tests (Sharadar field semantics incl. the
  split-adjustment recovery, GICS mapping, F-score math parity, bulk manifest
  skip logic, from-zero/update orchestration).
- `python3 final_verify.py` — coverage assertions + completion report.

---

## Notes & history

See **`NOTES.md`** for the current state and operational notes. The full FMP-era
history (provider code, docs, logs, pre-wipe database) is archived under
**`archive/fmp-era/`**.
