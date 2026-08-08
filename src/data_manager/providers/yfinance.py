"""yfinance provider — free source for prices, classifications, and fundamentals."""

import yfinance as yf

from .base import BaseProvider


class YFinanceProvider(BaseProvider):
    name = "yfinance"

    def get_universe(self):
        raise NotImplementedError("Use FinancialDatasetsProvider for the universe.")

    def get_prices(self, ticker: str, start: str, end: str) -> list[dict]:
        """Return daily OHLCV rows for a ticker.

        Robust to yfinance's MultiIndex columns (Price, Ticker) seen with
        auto_adjust=False on recent yfinance versions.
        """
        import pandas as pd
        # yfinance uses dotted share-class tickers (BRK.B, BF.B); the universe CSV
        # gives BRKB/BFB - try the original, then the dotted variant.
        candidates = [ticker]
        if "." not in ticker and len(ticker) > 2:
            candidates.append(ticker[:-1] + "." + ticker[-1])   # BRKB -> BRK.B
            candidates.append(ticker[:-1] + "-" + ticker[-1])   # BRKB -> BRK-B
        df = None
        for cand in candidates:
            df = yf.download(cand, start=start, end=end, auto_adjust=False, progress=False)
            if df is not None and not df.empty:
                break
        if df is None or df.empty:
            return []
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.reset_index()
        date_col = "Date" if "Date" in df.columns else df.columns[0]
        rows = []
        for _, r in df.iterrows():
            d = r[date_col]
            d = d.date() if hasattr(d, "date") else d
            close = _num(r.get("Close"))
            adj = _num(r.get("Adj Close"))
            adjustment = (adj / close) if (close and adj) else 1.0
            rows.append({
                "date": str(d),
                "open": _num(r.get("Open")),
                "high": _num(r.get("High")),
                "low": _num(r.get("Low")),
                "close": close,
                "adjustment": adjustment,
                "volume": int(r.get("Volume")) if _num(r.get("Volume")) is not None else None,
            })
        return rows

    def get_ratios(self, ticker: str) -> dict:
        """Point-in-time fundamental ratios from yfinance Ticker().info (free).

        Returns a snapshot dict of valuation + quality ratios; missing keys are
        None. Best-effort: any failure returns {} so callers can skip quietly.
        """
        try:
            info = yf.Ticker(ticker).info
        except Exception:
            return {}
        keys = ["trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months",
                "returnOnEquity", "returnOnAssets", "profitMargins", "grossMargins",
                "operatingMargins", "debtToEquity", "currentRatio", "dividendYield",
                "marketCap", "enterpriseValue", "enterpriseToEbitda", "beta",
                "sharesOutstanding"]
        return {_rkey(k): _num(info.get(k)) for k in keys}

    def get_classification(self, ticker: str) -> dict:
        """Return {sector, industry} for a ticker."""
        try:
            info = yf.Ticker(ticker).info
            return {
                "sector": info.get("sector"),
                "industry": info.get("industry"),
            }
        except Exception:
            return {"sector": None, "industry": None}

    def get_fundamentals(self, ticker: str) -> list[dict]:
        """Return annual fundamental rows for a ticker (Piotroski F-Score inputs).

        Computes the 9 Piotroski F-Score signals from income statement, balance
        sheet, and cash flow. Returns one row per fiscal year.
        """
        t = yf.Ticker(ticker)
        try:
            income = t.income_stmt  # columns = fiscal years
            balance = t.balance_sheet
            cashflow = t.cashflow
        except Exception:
            return []
        if income is None or income.empty:
            return []
        years = [c for c in income.columns]
        rows = []
        for i, yr in enumerate(years):
            row = _fscore_row(income, balance, cashflow, years, i)
            if row:
                rows.append(row)
        return rows


_RATIO_KEYS = {
    "trailing_pe": "trailingPE", "forward_pe": "forwardPE", "price_to_book": "priceToBook",
    "price_to_sales": "priceToSalesTrailing12Months", "roe": "returnOnEquity",
    "roa": "returnOnAssets", "net_margin": "profitMargins", "gross_margin": "grossMargins",
    "operating_margin": "operatingMargins", "debt_to_equity": "debtToEquity",
    "current_ratio": "currentRatio", "dividend_yield": "dividendYield",
    "market_cap": "marketCap", "enterprise_value": "enterpriseValue",
    "ev_to_ebitda": "enterpriseToEbitda", "beta": "beta",
    "shares_outstanding": "sharesOutstanding",
}


def _rkey(yf_key: str) -> str:
    for k, v in _RATIO_KEYS.items():
        if v == yf_key:
            return k
    return yf_key


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _safe_div(a, b):
    """Division that returns None on missing/zero inputs instead of raising."""
    try:
        if a is None or b is None or b == 0:
            return None
        return a / b
    except Exception:
        return None


def _fscore_row(income, balance, cashflow, years, i):
    """Compute one Piotroski F-Score row for fiscal year years[i]."""
    yr = years[i]
    prev = years[i + 1] if i + 1 < len(years) else None

    def g(df, col, year):
        try:
            v = df.loc[col, year]
            return _num(v)
        except Exception:
            return None

    # Current year values
    net_income = g(income, "Net Income", yr)
    total_assets = g(balance, "Total Assets", yr)
    if net_income is None and total_assets is None:
        return None  # year has no data at all - skip rather than store a fake F=0
    cfo = g(cashflow, "Operating Cash Flow", yr)
    total_liab = g(balance, "Total Liabilities Net Minority Interest", yr)
    current_assets = g(balance, "Current Assets", yr)
    current_liab = g(balance, "Current Liabilities", yr)
    shares = g(balance, "Share Issued", yr)
    gross_profit = g(income, "Gross Profit", yr)
    revenue = g(income, "Total Revenue", yr)

    # Prior year values
    p_net_income = g(income, "Net Income", prev) if prev else None
    p_total_assets = g(balance, "Total Assets", prev) if prev else None
    p_cfo = g(cashflow, "Operating Cash Flow", prev) if prev else None
    p_total_liab = g(balance, "Total Liabilities Net Minority Interest", prev) if prev else None
    p_current_assets = g(balance, "Current Assets", prev) if prev else None
    p_current_liab = g(balance, "Current Liabilities", prev) if prev else None
    p_shares = g(balance, "Share Issued", prev) if prev else None
    p_gross_profit = g(income, "Gross Profit", prev) if prev else None
    p_revenue = g(income, "Total Revenue", prev) if prev else None

    # F-Score signals (1 if good, 0 otherwise) - all divisions None/zero-safe
    roa   = _safe_div(net_income, total_assets)
    p_roa = _safe_div(p_net_income, p_total_assets)
    cfo_pos = 1 if (cfo is not None and cfo > 0) else 0
    d_roa = 1 if (roa is not None and p_roa is not None and roa > p_roa) else 0
    accruals = 1 if (cfo is not None and net_income is not None and cfo > net_income) else 0
    d_leverage = 1 if (total_liab is not None and p_total_liab is not None and total_liab < p_total_liab) else 0
    lr  = _safe_div(current_assets, current_liab)
    plr = _safe_div(p_current_assets, p_current_liab)
    d_liquidity = 1 if (lr is not None and plr is not None and lr > plr) else 0
    equity_issuance = 1 if (shares is not None and p_shares is not None and shares <= p_shares) else 0
    gm  = _safe_div(gross_profit, revenue)
    pgm = _safe_div(p_gross_profit, p_revenue)
    d_gross_margin = 1 if (gm is not None and pgm is not None and gm > pgm) else 0
    at  = _safe_div(revenue, total_assets)
    pat = _safe_div(p_revenue, p_total_assets)
    d_asset_turnover = 1 if (at is not None and pat is not None and at > pat) else 0

    f_score = sum([cfo_pos, d_roa, accruals, d_leverage, d_liquidity,
                   equity_issuance, d_gross_margin, d_asset_turnover])

    return {
        "ticker": None,  # filled by caller
        "fiscal_year": int(yr.year) if hasattr(yr, "year") else int(yr),
        "roa": roa,
        "cfo": cfo,
        "d_roa": d_roa,
        "accruals": accruals,
        "d_leverage": d_leverage,
        "d_liquidity": d_liquidity,
        "equity_issuance": equity_issuance,
        "d_gross_margin": d_gross_margin,
        "d_asset_turnover": d_asset_turnover,
        "f_score": f_score,
    }
