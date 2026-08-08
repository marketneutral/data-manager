# data-manager

Acquisition, storage, and point-in-time market data for the quant research stack —
the data backbone for **Numerai Signals strategies**, the **risk model**, and the
**stock-selection model**.

- **Universe:** Russell 3000 (2,589 tickers) from the free iShares IWV holdings CSV.
- **Prices:** 10 years of **as-of OHLCV + an adjustment factor** (apply at query time).
- **Fundamentals:** 10 years of annual **Piotroski F-Score with all 9 components**.
- **Ratios:** point-in-time TTM valuation/quality ratios.
- **Quarterly:** ~10 years of quarterly income/balance/cash-flow statements.
- **Storage:** a single local SQLite database (`~/.prime/agent/data_manager.db`).

Everything is **free or fixed-cost**, resumable, paced, and reproducible via the CLI.

---

## Quick start

```bash
cd ~/dev/data-manager
uv venv && uv pip install -e .        # one-time setup
export FMP_API_KEY=...                # from ~/.env (fixed-cost plan; 750 calls/min cap)

uv run data-manager status            # live coverage snapshot
uv run data-manager update-universe   # R3000 from iShares IWV (free)
uv run data-manager enrich-cik        # CIK from SEC
uv run data-manager enrich-figi       # FIGI from OpenFigi
uv run data-manager enrich-sic        # SIC + description + LEI from SEC
uv run data-manager update-classifications --all
uv run data-manager update-prices --all --start 2016-08-01   # 10y as-of OHLCV + adjustment
uv run data-manager update-fundamentals --all               # FMP annual, 10y, F-score components
uv run data-manager update-ratios --all                     # FMP TTM ratio snapshots
uv run data-manager update-quarterly --all                   # FMP quarterly statements, ~10y
python3 make_spec_sheet.py            # regenerate data_quality_report.html
```

---

## Architecture

```
providers/            data sources (pluggable, auto-selected)
  ishares.py            IWV holdings CSV  -> universe + sector
  financialdatasets.py  paid alternative (kept, not default)
  yfinance.py           prices (10y)      -> as-of OHLCV + adjustment
  fmp.py                fundamentals, ratios, quarterly, classification (FMP)
universe.py           orchestration: update_* functions (resumable, paced, resilient)
enrich.py             security-master enrichment (FIGI/CIK/SIC/LEI)
db.py                 SQLite schema + connection (WAL, busy_timeout)
cli.py                `data-manager` command line
make_spec_sheet.py    builds data_quality_report.html (coverage/missingness/timeframes)
final_verify.py       end-of-pipeline assertions
```

**Provider auto-selection:** `universe._default_data_provider()` returns **FMP** when
`FMP_API_KEY` is present, else **yfinance**. Prices stay on yfinance (FMP's price
history is tier-capped at ~5y on the current key); fundamentals/ratios/quarterly use FMP.

---

## Database schema (`~/.prime/agent/data_manager.db`)

### `universe` — security master (PK: ticker)
| col | meaning |
|---|---|
| ticker, name, source, added_at | identity + provenance |
| figi | OpenFigi identifier |
| cik | SEC Central Index Key (zero-padded) |
| sic, sic_description | SEC SIC code + description |
| lei | Legal Entity Identifier (mostly NULL from SEC — see gaps) |

### `prices` — as-of OHLCV + adjustment (PK: ticker, date)
| col | meaning |
|---|---|
| date, open, high, low, close, volume | **raw as-of** prices (as traded that day) |
| adjustment | per-day factor = `Adj Close / Close` (captures splits **and** dividends) |

> **Adjust at query time:** `adjusted_prices(ticker, start, end)` returns
> `adjusted_open/high/low/close = raw × adjustment`. Volume stays as traded.
> This keeps raw prices intact and lets you choose adjustment convention per query.

### `classifications` (PK: ticker)
sector (from IWV CSV), industry (yfinance/FMP), as_of.

### `fundamentals` — Piotroski F-Score (PK: ticker, fiscal_year)
| col | meaning |
|---|---|
| roa, cfo | raw return-on-assets and operating cash flow |
| d_roa, accruals, d_leverage, d_liquidity, equity_issuance, d_gross_margin, d_asset_turnover | the **9 F-score signals** (0/1) |
| f_score | composite 0–9 |

> The **components are stored**, not just the score — you can rebuild or re-weight
> the F-score, or use individual signals as features.

### `ratios` — point-in-time TTM snapshot (PK: ticker, as_of)
trailing_pe, forward_pe, price_to_book, price_to_sales, roe, roa, net_margin,
gross_margin, operating_margin, debt_to_equity, current_ratio, dividend_yield,
market_cap, enterprise_value, ev_to_ebitda, beta, shares_outstanding.
Units are FMP-native: margins/ROE as fractions (0.34 = 34%), dividend_yield raw
fraction (0.0042 = 0.42%).

### `quarterly_statements` — high-resolution fundamentals (PK: ticker, period)
net_income, revenue, gross_profit, operating_cash_flow, total_assets,
total_liabilities, current_assets, current_liabilities, shares_out, roa, cfo —
one row per fiscal quarter (~10 years, `period=quarter&limit=40`).

### `snapshots`
Provenance: source, pulled_at, as_of, row_count for each universe pull.

---

## CLI reference

```
data-manager status                     live counts + identifier coverage
data-manager update-universe            R3000 from iShares IWV (free)
data-manager enrich-cik                 CIK from SEC company_tickers.json
data-manager enrich-figi                FIGI from OpenFigi (batches of 10, resumable)
data-manager enrich-sic [--max N]       SIC + description + LEI from SEC submissions
data-manager update-prices --all --start YYYY-MM-DD [--end ...] [--ticker A,B]
data-manager update-classifications --all [--ticker A,B]
data-manager update-fundamentals --all [--ticker A,B]
data-manager update-ratios --all [--ticker A,B]
data-manager update-quarterly --all [--ticker A,B]
```

All `update-*` jobs are **resumable** (skip already-covered tickers), **resilient**
(per-ticker errors are logged, not fatal), **paced** (respect provider rate limits),
and **commit incrementally** (progress is visible in `status` and survives interruption).

---

## Data sources & rate limits

| Source | Used for | Cost | Limits |
|---|---|---|---|
| iShares IWV CSV | universe + sector | free | — |
| SEC (company_tickers, submissions) | CIK, SIC, LEI | free | polite ~6-7 req/s; needs User-Agent |
| OpenFigi | FIGI | free | batch ≤10; ~2-5 req/s; 429 backoff |
| yfinance | 10y prices | free | sequential only; YFRateLimitError on parallel |
| **FMP** | fundamentals, ratios, quarterly, classification | **fixed cost** | **750 calls/min**; price history tier-capped ~5y |

> **FMP cost rule:** fixed-cost plan — bulk runs are fine, but stay under the
> **750 calls/min** cap (the pipeline paces itself well below it).

---

## Query-time adjustment example

```python
from data_manager import adjusted_prices
rows = adjusted_prices("AAPL", "2020-03-16", "2020-03-20")
for r in rows:
    print(r["date"], r["close"], "->", r["adjusted_close"])
```

---

## Spec sheet & verification

- `python3 make_spec_sheet.py` → **`data_quality_report.html`** — how much data,
  missingness per identifier, timeframes, F-score histogram, ratio fill rates.
- `python3 final_verify.py` — asserts coverage minima (prices ≥75% of universe,
  fundamentals > 0, ratios > 0, SIC > 2,500) and prints the completion report.

---

## Known gaps (documented, not errors)

- **LEI:** only 5/2,589 — SEC only carries LEIs companies registered. A GLEIF pass
  could fill more if entity-level disambiguation is ever needed.
- **Industry:** ~1,287 missing — non-equity CSV artifacts (futures/cash-collateral
  rows like `ESU6`, `MSFUT`) and delisted names.
- **Prices:** 12/2,589 tickers have no data — futures/cash artifacts + genuinely
  delisted names (never recover).
- **FMP bulk endpoints** returned empty on this plan; per-ticker calls are the path.

---

## Notes & history

See **`NOTES.md`** for the full operational history: the financialdatasets.ai credit
incident, the iShares IWV switch, the FMP evaluation + migration, pipeline ops,
and the final-state summary.
