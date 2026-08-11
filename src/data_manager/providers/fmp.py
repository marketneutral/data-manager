"""FMP provider — Financial Modeling Prep (sole data provider).

Key in FMP_API_KEY (see ~/.env). Serves everything: as-traded EOD prices
(split jumps preserved, via the /stable/ non-split-adjusted endpoint), annual
fundamentals, TTM ratios, quarterly statements, and profile/classifications.
All methods best-effort; failures return {} / [] so callers skip quietly.
"""

import os
import json
import urllib.request
import urllib.error
from .base import BaseProvider


def _key() -> str:
    k = os.environ.get("FMP_API_KEY", "")
    if not k:
        env = os.path.expanduser("~/.env")
        if os.path.exists(env):
            for line in open(env):
                if line.startswith("FMP_API_KEY="):
                    k = line.split("=", 1)[1].strip()
    return k


def _get(path: str, **params) -> list | dict:
    return _fetch("https://financialmodelingprep.com/api/v3/", path, **params)


def _get_stable(path: str, **params) -> list:
    """Fetch from FMP's /stable/ API family (e.g. non-split-adjusted EOD prices)."""
    return _fetch("https://financialmodelingprep.com/stable/", path, **params)


def _fetch(base: str, path: str, **params) -> list | dict:
    key = _key()
    if not key:
        return []
    url = f"{base}{path}?apikey={key}"
    url += "".join(f"&{k}={v}" for k, v in params.items())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "data-manager/0.2"})
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode())
    except Exception as exc:
        print(f"[fmp] {path}: {type(exc).__name__}: {exc}", flush=True)
        return []


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _safe_div(a, b):
    try:
        if a is None or b is None or b == 0:
            return None
        return a / b
    except Exception:
        return None


class FMPProvider(BaseProvider):
    name = "fmp"

    def get_universe(self):
        raise NotImplementedError("Use ISharesProvider for the universe.")

    # ---- prices: AS-TRADED daily OHLCV + adjustment ----
    # close comes from FMP's non-split-adjusted EOD endpoint (/stable/ family):
    # raw prints with split jumps (e.g. TSLA 891.30 -> 296.07 at the 2022 3:1
    # split). The endpoint mislabels the fields adjOpen/adjHigh/adjLow/adjClose —
    # those ARE the as-traded values. adjustment = FMP's split+dividend-adjusted
    # adjClose (from historical-price-full) / as-traded close, i.e. the same
    # total-return factor semantics data-manager always used (Adj Close / Close).
    # FMP's ~5,000-row/request cap (about 20 years) means very deep ranges must
    # be fetched in backward chunks. Reported in NOTES.md (2026-08-11): one
    # request reaches ~20y; chunking reaches inception / the plan ceiling.

    def _paged_stable(self, path: str, symbol: str, start: str, end: str,
                      cap: int = 5000) -> list:
        """Stable-family fetch over [start, end] with automatic chunking.

        Returns merged rows newest-first (as the API returns them). Boundary
        overlaps between chunks are harmless: get_prices dedupes by date and
        the store is INSERT OR REPLACE on (ticker, date).
        """
        from datetime import datetime, timedelta
        merged: dict[str, dict] = {}
        to = end
        while True:
            rows = _get_stable(path, **{"symbol": symbol, "from": start, "to": to})
            if not isinstance(rows, list) or not rows:
                break
            for r in rows:
                merged[str(r.get("date"))] = r
            oldest = str(rows[-1].get("date", ""))
            if len(rows) < cap or not oldest or oldest <= start:
                break
            to = (datetime.strptime(oldest, "%Y-%m-%d")
                  - timedelta(days=1)).strftime("%Y-%m-%d")
        return [merged[d] for d in sorted(merged, reverse=True)]

    def _paged_v3_full(self, symbol: str, start: str, end: str,
                       cap: int = 5000) -> dict:
        """historical-price-full with automatic chunking (adjustment series)."""
        from datetime import datetime, timedelta
        merged: dict[str, dict] = {}
        to = end
        while True:
            d = _get("historical-price-full/" + symbol, **{"from": start, "to": to})
            hist = d.get("historical", []) if isinstance(d, dict) else []
            if not hist:
                break
            for r in hist:
                merged[str(r.get("date"))] = r
            oldest = str(hist[-1].get("date", ""))
            if len(hist) < cap or not oldest or oldest <= start:
                break
            to = (datetime.strptime(oldest, "%Y-%m-%d")
                  - timedelta(days=1)).strftime("%Y-%m-%d")
        return {"symbol": symbol,
                "historical": [merged[d] for d in sorted(merged, reverse=True)]}

    def get_prices(self, ticker: str, start: str, end: str) -> list[dict]:
        # FMP stores class shares under dash variants (BRK.B -> BRK-B); the plain
        # universe ticker (BRKB) returns an empty list. Try the dash variant when
        # the plain one is empty (dots are rejected with HTTP 402, so not tried).
        candidates = [ticker]
        if "-" not in ticker and "." not in ticker and len(ticker) > 2:
            candidates.append(ticker[:-1] + "-" + ticker[-1])   # BRKB -> BRK-B
        raw = []
        cand = ticker
        # FMP's no-params default is ~the last 1,254 rows (~5y). The full plan
        # depth (>=10y) requires an explicit from/to; the /stable/ EOD endpoint
        # honors from/to (start_date/end_date are ignored).
        for cand in candidates:
            if start:
                raw = self._paged_stable("historical-price-eod/non-split-adjusted",
                                         cand, start, end)
            else:
                raw = _get_stable("historical-price-eod/non-split-adjusted",
                                  symbol=cand)
            if isinstance(raw, list) and raw:
                break
        if not isinstance(raw, list) or not raw:
            return []
        adj = (self._paged_v3_full(cand, start, end) if start
               else _get("historical-price-full/" + cand))
        adj_by_date = {}
        if isinstance(adj, dict):
            for r in adj.get("historical", []):
                adj_by_date[str(r.get("date"))] = r
        rows = []
        for r in raw:  # raw is newest-first; we sort at the end
            d = str(r.get("date", ""))
            if d < start or d > end:
                continue
            close = _num(r.get("adjClose"))  # as-traded close on this endpoint
            ar = adj_by_date.get(d, {})
            adj_close = _num(ar.get("adjClose"))
            rows.append({
                "date": d,
                "open": _num(r.get("adjOpen")),
                "high": _num(r.get("adjHigh")),
                "low": _num(r.get("adjLow")),
                "close": close,
                "adjustment": (adj_close / close) if (close and adj_close) else 1.0,
                "volume": int(r["volume"]) if _num(r.get("volume")) is not None else None,
            })
        rows.sort(key=lambda x: x["date"])
        return rows

    # ---- classification from profile ----
    def get_classification(self, ticker: str) -> dict:
        p = _get("profile/" + ticker)
        if isinstance(p, list) and p:
            return {"sector": p[0].get("sector"), "industry": p[0].get("industry")}
        return {}

    # ---- Piotroski F-Score components from annual statements ----
    def get_fundamentals(self, ticker: str) -> list[dict]:
        inc = _get("income-statement/" + ticker, period="annual", limit="10")
        bal = _get("balance-sheet-statement/" + ticker, period="annual", limit="10")
        cas = _get("cash-flow-statement/" + ticker, period="annual", limit="10")
        if not isinstance(inc, list) or not inc:
            return []
        by = {str(r.get("date")): r for r in bal} if isinstance(bal, list) else {}
        cfy = {str(r.get("date")): r for r in cas} if isinstance(cas, list) else {}
        rows = []
        years = [str(r.get("date")) for r in inc]
        for i, yr in enumerate(years):
            r = inc[i]
            b = by.get(yr, {})
            c = cfy.get(yr, {})
            prev = inc[i + 1] if i + 1 < len(years) else None
            pb = by.get(prev.get("date"), {}) if prev else {}
            pc = cfy.get(prev.get("date"), {}) if prev else {}

            net_income = _num(r.get("netIncome"))
            total_assets = _num(b.get("totalAssets"))
            cfo = _num(c.get("operatingCashFlow"))
            total_liab = _num(b.get("totalLiabilities"))
            cur_a = _num(b.get("totalCurrentAssets"))
            cur_l = _num(b.get("totalCurrentLiabilities"))
            shares = _num(r.get("weightedAverageShsOutDil")) or _num(r.get("weightedAverageShsOut"))
            gross = _num(r.get("grossProfit"))
            revenue = _num(r.get("revenue"))
            if net_income is None and total_assets is None:
                continue

            p_net_income = _num(prev.get("netIncome")) if prev else None
            p_total_assets = _num(pb.get("totalAssets")) if prev else None
            p_cfo = _num(pc.get("operatingCashFlow")) if prev else None
            p_total_liab = _num(pb.get("totalLiabilities")) if prev else None
            p_cur_a = _num(pb.get("totalCurrentAssets")) if prev else None
            p_cur_l = _num(pb.get("totalCurrentLiabilities")) if prev else None
            p_shares = (_num(prev.get("weightedAverageShsOutDil")) or _num(prev.get("weightedAverageShsOut"))) if prev else None
            p_gross = _num(prev.get("grossProfit")) if prev else None
            p_revenue = _num(prev.get("revenue")) if prev else None

            roa = _safe_div(net_income, total_assets)
            p_roa = _safe_div(p_net_income, p_total_assets)
            cfo_pos = 1 if (cfo is not None and cfo > 0) else 0
            d_roa = 1 if (roa is not None and p_roa is not None and roa > p_roa) else 0
            accruals = 1 if (cfo is not None and net_income is not None and cfo > net_income) else 0
            d_leverage = 1 if (total_liab is not None and p_total_liab is not None and total_liab < p_total_liab) else 0
            lr = _safe_div(cur_a, cur_l)
            plr = _safe_div(p_cur_a, p_cur_l)
            d_liquidity = 1 if (lr is not None and plr is not None and lr > plr) else 0
            equity_issuance = 1 if (shares is not None and p_shares is not None and shares <= p_shares) else 0
            gm = _safe_div(gross, revenue)
            pgm = _safe_div(p_gross, p_revenue)
            d_gross_margin = 1 if (gm is not None and pgm is not None and gm > pgm) else 0
            at = _safe_div(revenue, total_assets)
            pat = _safe_div(p_revenue, p_total_assets)
            d_asset_turnover = 1 if (at is not None and pat is not None and at > pat) else 0

            rows.append({
                "fiscal_year": int(yr[:4]),
                "roa": roa, "cfo": cfo,
                "d_roa": d_roa, "accruals": accruals, "d_leverage": d_leverage,
                "d_liquidity": d_liquidity, "equity_issuance": equity_issuance,
                "d_gross_margin": d_gross_margin, "d_asset_turnover": d_asset_turnover,
                "f_score": sum([cfo_pos, d_roa, accruals, d_leverage, d_liquidity,
                                equity_issuance, d_gross_margin, d_asset_turnover]),
            })
        return rows

    # ---- quarterly statements (~10y of quarters) ----
    def get_quarterly(self, ticker: str) -> list[dict]:
        inc = _get("income-statement/" + ticker, period="quarter", limit="40")
        bal = _get("balance-sheet-statement/" + ticker, period="quarter", limit="40")
        cas = _get("cash-flow-statement/" + ticker, period="quarter", limit="40")
        if not isinstance(inc, list) or not inc:
            return []
        by = {str(r.get("date")): r for r in bal} if isinstance(bal, list) else {}
        cfy = {str(r.get("date")): r for r in cas} if isinstance(cas, list) else {}
        rows = []
        for r in inc:
            d = str(r.get("date"))
            b = by.get(d, {})
            c = cfy.get(d, {})
            ni = _num(r.get("netIncome")); ta = _num(b.get("totalAssets"))
            rows.append({
                "period": d,
                "net_income": ni, "revenue": _num(r.get("revenue")),
                "gross_profit": _num(r.get("grossProfit")),
                "operating_cash_flow": _num(c.get("operatingCashFlow")),
                "total_assets": ta, "total_liabilities": _num(b.get("totalLiabilities")),
                "current_assets": _num(b.get("totalCurrentAssets")),
                "current_liabilities": _num(b.get("totalCurrentLiabilities")),
                "shares_out": _num(r.get("weightedAverageShsOutDil")) or _num(r.get("weightedAverageShsOut")),
                "roa": _safe_div(ni, ta),
                "cfo": _num(c.get("operatingCashFlow")),
            })
        return rows

    # ---- ratio snapshot (TTM) ----
    def get_ratios(self, ticker: str) -> dict:
        rt = _get("ratios-ttm/" + ticker)
        km = _get("key-metrics-ttm/" + ticker)
        pr = _get("profile/" + ticker)
        a = rt[0] if isinstance(rt, list) and rt else {}
        b = km[0] if isinstance(km, list) and km else {}
        p = pr[0] if isinstance(pr, list) and pr else {}
        return {
            "trailing_pe": _num(a.get("priceEarningsRatioTTM")),
            "forward_pe": None,  # not exposed cleanly on TTM endpoints
            "price_to_book": _num(a.get("priceToBookRatioTTM")),
            "price_to_sales": _num(b.get("priceToSalesRatioTTM")),
            "roe": _num(b.get("roeTTM")),
            "roa": _num(b.get("roaTTM")),
            "net_margin": _num(b.get("netProfitMarginTTM")),
            "gross_margin": _num(b.get("grossProfitMarginTTM")),
            "operating_margin": _num(b.get("operatingProfitMarginTTM")),
            "debt_to_equity": _num(b.get("debtToEquityTTM")),
            "current_ratio": _num(b.get("currentRatioTTM")),
            "dividend_yield": _num(b.get("dividendYieldTTM")),
            "market_cap": _num(p.get("marketCap")),
            "enterprise_value": _num(p.get("enterpriseValue")),
            "ev_to_ebitda": None,
            "beta": _num(p.get("beta")),
            "shares_outstanding": None,
        }
