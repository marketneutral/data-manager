# Data Provider Recommendation — As-Traded OHLCV First, One Provider if Possible

**Date:** 2026-08-08 · **Status:** VERIFIED — as-traded data confirmed live on the current Premium key (no upgrade needed); costs verified against vendor sites 2026-08-08

## 1. What we need (in priority order)

1. **As-traded daily OHLCV** for the R3000 universe, ≥10y — raw close with **split jumps**
   visible (e.g. TSLA: 2022-08-24 = $891.29 → 2022-08-25 = $296.43; AAPL 2020: $499.23 → $129.04).
   This is the *hard requirement we do not have today*: FMP `historical-price-full`,
   yfinance, and Yahoo's chart API all serve **split-continuous** closes here (verified:
   FMP TSLA 2022-08-24 = 297.10 = 891.29 ÷ 3 exactly; Yahoo chart close 2020-08-28 =
   124.81 = 499.23 ÷ 4).
2. Everything else data-manager already uses (ideally same vendor): **annual fundamentals**
   (Piotroski inputs), **TTM ratios**, **quarterly statements**, **classifications**.
3. Bulk-friendly (2,589 tickers), 10y depth, affordable, resumable.

## 2. FMP pricing ladder (verified on site.financialmodelingprep.com, 2026-08-08)

| Plan | Price (billed annually) | Calls | Timeframe | Price-history depth | Key contents |
|---|---|---|---|---|---|
| Basic | Free/entry | 250 calls/**day** | End of Day | 5y | 150+ endpoints, profile/reference |
| **Starter** | **$22.00/mo** | 300 calls/min | Real-time | **up to 5y** | annual fundamentals & ratios, historical stock prices, news, crypto/forex |
| **Premium** | **$59.00/mo** | 750 calls/min | Real-time | **30+ years** | full fundamentals & ratios, intraday charts, technical indicators, corporate calendars, DCF, UK/Canada |
| Ultimate | $149.00/mo | 3,000 calls/min | Real-time | 30+ years | global coverage, earnings-call transcripts, ETF/MF holdings, 13F, 1-min intraday, **bulk/batch delivery** |
| Build / Enterprise | contact | — | — | — | custom |

Monthly (non-annual) billing is pricier; annual is the standard recommendation.
(README's "750 calls/min" matches today's *Premium* row; the observed **~5y FMP depth cap**
matches today's *Starter* row — verify which plan the key is on in the FMP dashboard.)

**Answer to "what is the cost of the next tier of FMP?":** from the plan that caps price
history at ~5y (Starter, $22/mo annual) the next tier is **Premium at $59/mo annual
(~$79/mo if billed monthly, ~2.7× Starter)** — which per FMP's matrix raises historical
depth to **30+ years** and unlocks **full fundamentals/ratios + intraday charts + calendars**.

### FMP's as-traded (unadjusted) product — same vendor
FMP ships a dedicated endpoint (docs: "without adjustments for stock splits … see how
stock prices moved before and after stock splits"):

```
GET https://financialmodelingprep.com/stable/historical-price-eod/non-split-adjusted?symbol=TSLA   ✅ WORKS on Premium key
```

**VERIFIED live on the Premium key (2026-08-08)** — NOTE: the endpoint must be hit via the
`/stable/` host prefix; the legacy `/api/v3/historical-price-eod-non-split-adjusted` path
returns `[]`. Response fields are misleadingly named `adjOpen/adjHigh/adjLow/adjClose` —
on this non-split-adjusted endpoint these ARE the raw as-traded values.

Split-jump proof (as-traded, split jumps visible):
- TSLA 3:1 (2022-08-25): close **891.30 → 296.07** (÷3 ✔), volume 19.1M → 53.2M (×3 ✔)
- SHOP 10:1 (2022-06-29): close **350.26 → 33.05** (÷10 ✔), volume 4.4M → 30.9M (×10 ✔)
- Same window via split-adjusted `historical-price-full`: flat/continuous (TSLA 297.10
  both sides) — confirming the two endpoints differ exactly by the split factor.

**Depth caveat observed in this environment:** every FMP price endpoint (adjusted and
unadjusted, with `timeseries=10000`) returns ~1,254 rows = **~5y (2021-08-10 → 2026-08-07)**,
even on a Premium key. FMP's pricing matrix advertises 30+ years on Premium, so if ≥10y
as-traded is required, confirm the depth with FMP support (or expect to source the older
5y from elsewhere). FMP's FAQ confirms their *standard* endpoints are split-adjusted:
"the 'close' price is adjusted only for stock splits and 'adjClose' is adjusted for stock
splits and dividends" — so regular `historical-price-full` can never give as-traded closes;
the non-split-adjusted endpoint is the correct source.

## 3. FMP same-provider dataset coverage (data-manager needs)

| data-manager need | FMP endpoint family | Starter | Premium |
|---|---|---|---|
| As-traded OHLCV (split jumps) | `historical-price-eod/non-split-adjusted` | ❌ gated | ✅ (verify) |
| Adjusted OHLCV (backward-compat) | `historical-price-full`, adjusted EOD | ✅ 5y | ✅ 30y+ |
| Annual fundamentals / F-score inputs | income/balance/cash-flow statements | ✅ annual | ✅ full |
| TTM ratios | `ratios-ttm` | ✅ | ✅ |
| Quarterly statements | `income-statement?period=quarter` | — | ✅ |
| Classifications (sector/industry) | company profile, screener | ✅ | ✅ |
| Splits/dividends calendars (reconstruction aid) | stock-split-calendar, dividends-calendar | — | ✅ |
| Intraday | `historical-chart/1min..4h` | — | ✅ (1-min on Ultimate) |

**Verdict:** FMP is the only candidate that covers *everything* data-manager uses today
plus as-traded prices with one subscription — the natural "single provider".

## 4. Alternatives (as-traded OHLCV + breadth, costs verified 2026-08-08)

| Provider | As-traded mechanism (verified) | Cost | Fundamentals/statements? | Single-provider? |
|---|---|---|---|---|
| **EODHD** | raw OHLC **by default** (their docs: "OHLC values are raw — adjusted for neither splits nor dividends"; `adjusted_close` separate) | EOD All World **$19.99/mo**; +Intraday $29.99/mo; Fundamentals feed **$59.99/mo**; **All-In-One $99.99/mo** ($999.90/yr) | ✅ separate Fundamentals Data Feed | ⚠️ costs ~$100/mo bundled |
| **Polygon.io** | `adjusted=false` on aggregates (docs: "not adjusted for splits") | Free 5 calls/min; Starter ~$29/mo | ❌ none (reference + events only) | ❌ |
| **Tiingo** | `adjustSplit=false&adjustDividends=false`; separate splits/dividends APIs | Free ~50 symbols/hr; paid ~$20/mo | ⚠️ third-party add-on | ❌ |
| **Alpha Vantage** | `TIME_SERIES_DAILY` is raw as-traded, 20+y (official docs) | Free 25 req/day (bulk-impractical) | ✅ but rate-limited | ❌ for R3000 |

## 5. Recommendation

1. **Use FMP as-is (Premium key already active) — NO upgrade needed for as-traded.**
   Point `FMPProvider.get_prices` at
   `https://financialmodelingprep.com/stable/historical-price-eod/non-split-adjusted?symbol={ticker}`
   and store the returned `adjClose` (which is the as-traded close on this endpoint) as
   `prices.close`. Verified on two splits (TSLA 3:1, SHOP 10:1); the split jumps and
   as-traded volume are present.
   - Adjustment factor: `factor = split_adjusted_close / as_traded_close` using
     `historical-price-full` for the same date (equals the split factor on no-dividend
     names; add the dividend-adjusted series for full total-return factors).
   - Field-name gotcha: endpoint returns `adjOpen/adjHigh/adjLow/adjClose` even though it
     is the *non*-split-adjusted product — these are the raw as-traded values.
2. **Depth check before reloading the 10y table:** this environment returns ~5y depth on
   every FMP price endpoint even with `timeseries=10000`. If data-manager must go back to
   2016-08-01 with as-traded closes, verify real depth on the key (FMP matrix advertises
   30+y on Premium); if still 5y, only the last ~5y can be as-traded from FMP and the
   older split-continuous yfinance window can't be blended in — decide with FMP support.
3. **Fallback if FMP depth disappoints: EODHD** — raw OHLC is the default convention,
   matches data-manager's `close` + `adjustment` schema natively
   (`adjustment = adjusted_close / close`), $19.99/mo for prices alone; keep FMP for
   fundamentals (two-provider hybrid ≈ $42/mo) or take EODHD All-In-One at $99.99/mo.
4. **Avoid for now:** Polygon/Tiingo (no fundamentals → fails single-provider),
   Alpha Vantage (rate limits), yfinance (split-continuous in this environment).

## 6. Sources
- FMP pricing: site.financialmodelingprep.com/developer/docs/pricing + /pricing-plans (2026-08-08, annual-billing prices)
- FMP unadjusted-endpoint docs: /developer/docs/stable/historical-price-eod-non-split-adjusted ("Unadjusted Stock Price Chart API" = OHLCV without split adjustments)
- FMP FAQ (adjustment semantics: close = split-adjusted; adjClose = splits+dividends)
- EODHD pricing: eodhd.com/pricing (EOD $19.99, +Intraday $29.99, Fundamentals $59.99, All-In-One $99.99)
- Polygon docs: polygon.readthedocs.io ("adjusted=False … NOT adjusted for splits")
- Tiingo docs/blog (raw prices "what actually printed on the tape"; adjustSplit/adjustDividends; splits & dividends APIs)
- Alpha Vantage docs (TIME_SERIES_DAILY = raw as-traded, 20+y; daily-adjusted is separate)
- Live endpoint tests on the current Premium key (2026-08-08):
  + `/stable/historical-price-eod/non-split-adjusted` → **as-traded with split jumps** (TSLA 891.30→296.07 @ 3:1, SHOP 350.26→33.05 @ 10:1, as-traded volume)
  + `/api/v3/historical-price-eod/non-split-adjusted` and legacy variants → `[]` (wrong base path — must use `/stable/`)
  + `historical-price-full` (split-adjusted, split-baked; TSLA 2022-08-24 close 297.10 = 891.29/3) — split-continuous
  + yfinance / Yahoo chart API — split-continuous (AAPL 2020-08-28 = 124.81 = 499.23/4)
  + FMP depth = ~1,254 rows (~5y, 2021-08-10 → 2026-08-07) on every price endpoint, `timeseries=10000` included
