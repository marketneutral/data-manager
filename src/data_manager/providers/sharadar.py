"""Sharadar data provider (the FMP replacement).

Covers everything data-manager used FMP for, plus what FMP could not:
survivorship-bias-free history to 1998 (active AND delisted names), a
securities master (tickers), corporate actions, point-in-time (as-reported)
fundamentals, and 3-way-adjusted EOD prices for stocks and funds.

Tables (api.sharadar.com/v1.0/data/<table>):
  tickers        securities master (sector! industry! delisted flags, dates)
  stocks         EOD OHLCV, active + delisted, 3 adjustment methods
  funds          EOD OHLCV for ETFs/ETNs/CEFs (same shape as stocks)
  fundamentals   SF1: 100+ indicators, AR (as-reported) / MR dimensions
  actions        splits, dividends, ticker changes, delistings...
  sp500          current + historical constituents + changes (to 1998)
  metrics        price-based market metrics

Returns rows in EXACTLY the shapes FMPProvider returned, so universe.py and
the database schema are untouched; switch via DATA_PROVIDER=sharadar.
"""
from __future__ import annotations

import csv
import io
import os
import urllib.request
from functools import lru_cache

from .base import BaseProvider

API = "https://api.sharadar.com/v1.0/data/{table}"


def _key() -> str:
    k = os.environ.get("SHARADAR_API_KEY", "")
    if k:
        return k
    try:
        p = os.path.expanduser("~/.env")
        if os.path.exists(p):
            for line in open(p):
                if line.startswith("SHARADAR_API_KEY"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _num(v):
    try:
        if v is None or v == "" or v == "N/A":
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


def _fetch(table: str, **params) -> list[dict]:
    """GET a Sharadar table as CSV -> list of row dicts (string values)."""
    key = _key()
    if not key:
        return []
    url = API.format(table=table)
    url += f"?api_key={key}&format=csv"
    url += "".join(f"&{k}={v}" for k, v in params.items())
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "data-manager/0.3"})
        with urllib.request.urlopen(req, timeout=90) as r:
            return list(csv.DictReader(io.StringIO(r.read().decode())))
    except Exception as exc:
        print(f"[sharadar] {table}: {type(exc).__name__}: {exc}", flush=True)
        return []


# --------------------------------------------------------------------------
# sector taxonomy: Sharadar's own labels -> the 11 GICS sectors our risk
# model's inclusion filter allowlists. Unmapped labels return None so the
# filter FAILS CLOSED (excluded). Verify against live data after subscribing.
# --------------------------------------------------------------------------
SHARADAR_TO_GICS = {
    "Technology": "Information Technology",
    "Electronic Technology": "Information Technology",
    "Technology Services": "Information Technology",
    "Healthcare": "Health Care",
    "Health Care": "Health Care",
    "Health Technology": "Health Care",
    "Health Services": "Health Care",
    "Finance": "Financials",
    "Financials": "Financials",
    "Finance & Insurance": "Financials",
    "Energy": "Energy",
    "Consumer Non-Durables": "Consumer Staples",
    "Consumer Staples": "Consumer Staples",
    "Consumer Durables": "Consumer Discretionary",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Services": "Consumer Discretionary",
    "Retail Trade": "Consumer Discretionary",
    "Producer Manufacturing": "Industrials",
    "Industrial Services": "Industrials",
    "Transportation": "Industrials",
    "Distribution Services": "Industrials",
    "Commercial Services": "Industrials",
    "Process Industries": "Materials",
    "Materials": "Materials",
    "Non-Energy Minerals": "Materials",
    "Utilities": "Utilities",
    "Communications": "Communication Services",
    "Communication Services": "Communication Services",
    "Real Estate": "Real Estate",
}


def gics_sector(raw: str | None) -> str | None:
    """Map a Sharadar sector label to an 11-GICS label (None if unmapped)."""
    if not raw:
        return None
    return SHARADAR_TO_GICS.get(raw.strip().strip('"'))


class SharadarProvider(BaseProvider):
    name = "sharadar"

    def get_universe(self):
        raise NotImplementedError("Universe stays with ISharesProvider (IWV).")

    # ---- which price table: funds (ETF/ETN/CEF) vs stocks ----
    @lru_cache(maxsize=8192)
    def _price_table(self, ticker: str) -> str:
        m = _fetch("tickers", ticker=ticker)
        if m and m[0].get("table"):
            return m[0]["table"]
        return "funds" if _fetch("funds", ticker=ticker) else "stocks"

    # ---- prices: as-traded OHLCV + adjustment factor ----
    def get_prices(self, ticker: str, start: str, end: str) -> list[dict]:
        table = self._price_table(ticker)
        rows = _fetch(table, ticker=ticker)
        out = []
        for r in rows:
            d = str(r.get("date", ""))
            if d < start or d > end:
                continue
            close = _num(r.get("close"))
            adj = _num(r.get("closeadj")) or _num(r.get("closeunadj"))
            out.append({
                "date": d,
                "open": _num(r.get("open")),
                "high": _num(r.get("high")),
                "low": _num(r.get("low")),
                "close": close,
                "adjustment": (adj / close) if (close and adj) else 1.0,
                "volume": int(float(r["volume"])) if _num(r.get("volume")) is not None else None,
            })
        out.sort(key=lambda x: x["date"])
        return out

    # ---- classification from the securities master ----
    def get_classification(self, ticker: str) -> dict:
        m = _fetch("tickers", ticker=ticker)
        if not m:
            return {}
        r = m[0]
        return {
            "sector": gics_sector(r.get("sector")),
            "industry": (r.get("industry") or "").strip() or None,
        }

    # ---- Piotroski F-Score, identical signal math to FMPProvider ----
    def get_fundamentals(self, ticker: str) -> list[dict]:
        rows = _fetch("fundamentals", ticker=ticker, dimension="ARY")
        if not rows:
            return []
        years = sorted(rows, key=lambda r: str(r.get("calendardate", "")))
        out = []
        for i, r in enumerate(years):
            prev = years[i - 1] if i > 0 else None

            net_income = _num(r.get("netinc"))
            total_assets = _num(r.get("assets"))
            if net_income is None and total_assets is None:
                continue
            cfo = _num(r.get("ncfo"))
            total_liab = _num(r.get("liabilities"))
            cur_a = _num(r.get("assetsc"))
            cur_l = _num(r.get("liabilitiesc"))
            shares = _num(r.get("shareswa")) or _num(r.get("sharesbas"))
            gross = _num(r.get("gp"))
            revenue = _num(r.get("revenue"))

            p_net_income = _num(prev.get("netinc")) if prev else None
            p_total_assets = _num(prev.get("assets")) if prev else None
            p_cfo = _num(prev.get("ncfo")) if prev else None
            p_total_liab = _num(prev.get("liabilities")) if prev else None
            p_cur_a = _num(prev.get("assetsc")) if prev else None
            p_cur_l = _num(prev.get("liabilitiesc")) if prev else None
            p_shares = (_num(prev.get("shareswa")) or _num(prev.get("sharesbas"))) if prev else None
            p_gross = _num(prev.get("gp")) if prev else None
            p_revenue = _num(prev.get("revenue")) if prev else None

            roa = _safe_div(net_income, total_assets)
            p_roa = _safe_div(p_net_income, p_total_assets)
            cfo_pos = 1 if (cfo is not None and cfo > 0) else 0
            d_roa = 1 if (roa is not None and p_roa is not None and roa > p_roa) else 0
            accruals = 1 if (cfo is not None and net_income is not None and cfo > net_income) else 0
            d_leverage = 1 if (total_liab is not None and p_total_liab is not None
                               and total_liab < p_total_liab) else 0
            lr = _safe_div(cur_a, cur_l)
            plr = _safe_div(p_cur_a, p_cur_l)
            d_liquidity = 1 if (lr is not None and plr is not None and lr > plr) else 0
            equity_issuance = 1 if (shares is not None and p_shares is not None
                                    and shares <= p_shares) else 0
            gm = _safe_div(gross, revenue)
            pgm = _safe_div(p_gross, p_revenue)
            d_gross_margin = 1 if (gm is not None and pgm is not None and gm > pgm) else 0
            at = _safe_div(revenue, total_assets)
            pat = _safe_div(p_revenue, p_total_assets)
            d_asset_turnover = 1 if (at is not None and pat is not None and at > pat) else 0

            out.append({
                "fiscal_year": int(str(r.get("calendardate", ""))[:4]),
                "roa": roa, "cfo": cfo,
                "d_roa": d_roa, "accruals": accruals, "d_leverage": d_leverage,
                "d_liquidity": d_liquidity, "equity_issuance": equity_issuance,
                "d_gross_margin": d_gross_margin, "d_asset_turnover": d_asset_turnover,
                "f_score": sum([cfo_pos, d_roa, accruals, d_leverage, d_liquidity,
                                equity_issuance, d_gross_margin, d_asset_turnover]),
            })
        return out

    # ---- quarterly statements from the ARQ dimension ----
    def get_quarterly(self, ticker: str) -> list[dict]:
        rows = _fetch("fundamentals", ticker=ticker, dimension="ARQ")
        out = []
        for r in rows:
            ni = _num(r.get("netinc"))
            ta = _num(r.get("assets"))
            out.append({
                "period": str(r.get("reportperiod") or r.get("calendardate")),
                "net_income": ni,
                "revenue": _num(r.get("revenue")),
                "gross_profit": _num(r.get("gp")),
                "operating_cash_flow": _num(r.get("ncfo")),
                "total_assets": ta,
                "total_liabilities": _num(r.get("liabilities")),
                "current_assets": _num(r.get("assetsc")),
                "current_liabilities": _num(r.get("liabilitiesc")),
                "shares_out": _num(r.get("shareswa")) or _num(r.get("sharesbas")),
                "roa": _safe_div(ni, ta),
                "cfo": _num(r.get("ncfo")),
            })
        return out

    # ---- ratio snapshot: latest MRY (annual most-recent) row ----
    def get_ratios(self, ticker: str) -> dict:
        rows = _fetch("fundamentals", ticker=ticker, dimension="MRY")
        if not rows:
            return {}
        r = sorted(rows, key=lambda x: str(x.get("calendardate", "")))[-1]
        rev = _num(r.get("revenue"))
        opinc = _num(r.get("opinc"))
        return {
            "trailing_pe": _num(r.get("pe")),
            "forward_pe": None,            # Sharadar has no analyst estimates
            "price_to_book": _num(r.get("pb")),
            "price_to_sales": _num(r.get("ps")),
            "roe": _num(r.get("roe")),
            "roa": _num(r.get("roa")),
            "net_margin": _num(r.get("netmargin")),
            "gross_margin": _num(r.get("grossmargin")),
            "operating_margin": _safe_div(opinc, rev),
            "debt_to_equity": _num(r.get("de")),
            "current_ratio": _num(r.get("currentratio")),
            "dividend_yield": _num(r.get("divyield")),
            "market_cap": _num(r.get("marketcap")),
            "enterprise_value": _num(r.get("ev")),
            "ev_to_ebitda": _num(r.get("evebitda")),
            "beta": None,                  # not provided; computed locally if needed
            "shares_outstanding": _num(r.get("shareswa")),
        }
