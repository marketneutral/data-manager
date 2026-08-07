"""yfinance provider — free source for prices, classifications, and fundamentals."""

import yfinance as yf

from .base import BaseProvider


class YFinanceProvider(BaseProvider):
    name = "yfinance"

    def get_universe(self):
        raise NotImplementedError("Use FinancialDatasetsProvider for the universe.")

    def get_prices(self, ticker: str, start: str, end: str) -> list[dict]:
        """Return daily OHLCV rows for a ticker."""
        df = yf.download(ticker, start=start, end=end, auto_adjust=False, progress=False)
        if df is None or df.empty:
            return []
        df = df.reset_index()
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "date": str(r["Date"].date()),
                "open": _num(r.get("Open")),
                "high": _num(r.get("High")),
                "low": _num(r.get("Low")),
                "close": _num(r.get("Close")),
                "adj_close": _num(r.get("Adj Close")),
                "volume": int(r.get("Volume")) if r.get("Volume") is not None else None,
            })
        return rows

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


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None  # drop NaN
    except (TypeError, ValueError):
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

    # F-Score signals (1 if good, 0 otherwise)
    roa = net_income / total_assets if total_assets else None
    cfo_pos = 1 if (cfo is not None and cfo > 0) else 0
    d_roa = 1 if (roa is not None and p_roa is not None and roa > p_roa) else 0
    p_roa = p_net_income / p_total_assets if p_total_assets else None
    accruals = 1 if (cfo is not None and net_income is not None and cfo > net_income) else 0
    d_leverage = 1 if (total_liab is not None and p_total_liab is not None and total_liab < p_total_liab) else 0
    d_liquidity = 1 if (current_assets is not None and current_liab is not None
                        and p_current_assets is not None and p_current_liab is not None
                        and (current_assets / current_liab) > (p_current_assets / p_current_liab)) else 0
    equity_issuance = 1 if (shares is not None and p_shares is not None and shares <= p_shares) else 0
    d_gross_margin = 1 if (gross_profit is not None and revenue is not None
                           and p_gross_profit is not None and p_revenue is not None
                           and (gross_profit / revenue) > (p_gross_profit / p_revenue)) else 0
    d_asset_turnover = 1 if (revenue is not None and total_assets is not None
                             and p_revenue is not None and p_total_assets is not None
                             and (revenue / total_assets) > (p_revenue / p_total_assets)) else 0

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
