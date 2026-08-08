"""Tests for data_manager.providers.yfinance — helpers + provider methods (mocked yf)."""

import pandas as pd
import pytest

from data_manager.providers import yfinance


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def test_num():
    assert yfinance._num(None) is None
    assert yfinance._num("3.5") == 3.5
    assert yfinance._num(float("nan")) is None
    assert yfinance._num("x") is None


def test_safe_div():
    assert yfinance._safe_div(9, 3) == 3.0
    assert yfinance._safe_div(9, 0) is None
    assert yfinance._safe_div(None, 3) is None


def test_rkey():
    assert yfinance._rkey("trailingPE") == "trailing_pe"
    assert yfinance._rkey("marketCap") == "market_cap"
    assert yfinance._rkey("enterpriseToEbitda") == "ev_to_ebitda"
    assert yfinance._rkey("unknownKey") == "unknownKey"


# --------------------------------------------------------------------------
# _fscore_row
# --------------------------------------------------------------------------

def _fs_years():
    return [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31")]


def _fs_statements():
    years = _fs_years()
    income = pd.DataFrame({
        "Net Income": [100, 80],
        "Gross Profit": [400, 300],
        "Total Revenue": [1000, 800],
    }, index=years).T
    balance = pd.DataFrame({
        "Total Assets": [1000, 900],
        "Total Liabilities Net Minority Interest": [500, 600],
        "Current Assets": [300, 250],
        "Current Liabilities": [200, 250],
        "Share Issued": [100, 100],
    }, index=years).T
    cashflow = pd.DataFrame({
        "Operating Cash Flow": [150, 90],
    }, index=years).T
    return income, balance, cashflow


def test_fscore_row_computes_fscore():
    income, balance, cashflow = _fs_statements()
    years = _fs_years()
    row = yfinance._fscore_row(income, balance, cashflow, years, 0)
    assert row["fiscal_year"] == 2025
    assert row["roa"] == pytest.approx(0.1)
    assert row["f_score"] == 8
    assert row["d_roa"] == 1
    assert row["accruals"] == 1
    assert row["d_leverage"] == 1
    assert row["d_liquidity"] == 1
    assert row["equity_issuance"] == 1
    assert row["d_gross_margin"] == 1
    assert row["d_asset_turnover"] == 1


def test_fscore_row_skips_empty_year():
    years = [pd.Timestamp("2025-12-31")]
    income = pd.DataFrame({"Net Income": [None]}, index=years).T
    balance = pd.DataFrame({"Total Assets": [None]}, index=years).T
    cashflow = pd.DataFrame({"Operating Cash Flow": [None]}, index=years).T
    assert yfinance._fscore_row(income, balance, cashflow, years, 0) is None


# --------------------------------------------------------------------------
# get_prices
# --------------------------------------------------------------------------

def test_yf_get_prices(monkeypatch):
    df = pd.DataFrame({
        "Open": [10.0, 10.5],
        "High": [11.0, 12.0],
        "Low": [9.0, 10.0],
        "Close": [10.5, 11.0],
        "Adj Close": [9.45, 9.9],
        "Volume": [1000, 2000],
    }, index=pd.to_datetime(["2026-01-02", "2026-01-03"]))
    calls = []

    def fake_download(ticker, start=None, end=None, auto_adjust=False, progress=False):
        calls.append(ticker)
        return df

    monkeypatch.setattr(yfinance.yf, "download", fake_download)
    rows = yfinance.YFinanceProvider().get_prices("AAPL", "2026-01-01", "2026-01-31")
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-01-02"
    assert rows[0]["adjustment"] == pytest.approx(9.45 / 10.5)
    assert rows[0]["volume"] == 1000
    assert calls[0] == "AAPL"


def test_yf_get_prices_multiindex(monkeypatch):
    cols = pd.MultiIndex.from_tuples([
        ("Open", "AAPL"), ("High", "AAPL"), ("Low", "AAPL"),
        ("Close", "AAPL"), ("Adj Close", "AAPL"), ("Volume", "AAPL")])
    df = pd.DataFrame([[10, 11, 9, 10.5, 9.45, 1000]], columns=cols,
                      index=pd.to_datetime(["2026-01-02"]))
    monkeypatch.setattr(yfinance.yf, "download", lambda *a, **k: df)
    rows = yfinance.YFinanceProvider().get_prices("AAPL", "2026-01-01", "2026-01-31")
    assert len(rows) == 1
    assert rows[0]["close"] == 10.5
    assert rows[0]["adjustment"] == pytest.approx(9.45 / 10.5)


def test_yf_get_prices_empty(monkeypatch):
    monkeypatch.setattr(yfinance.yf, "download", lambda *a, **k: None)
    assert yfinance.YFinanceProvider().get_prices("AAPL", "2026-01-01", "2026-01-31") == []


def test_yf_get_prices_tries_dotted_variant(monkeypatch):
    empty = pd.DataFrame()
    df = pd.DataFrame({
        "Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5],
        "Adj Close": [9.45], "Volume": [1000],
    }, index=pd.to_datetime(["2026-01-02"]))
    calls = []

    def fake_download(ticker, start=None, end=None, auto_adjust=False, progress=False):
        calls.append(ticker)
        return empty if ticker == "BRKB" else df

    monkeypatch.setattr(yfinance.yf, "download", fake_download)
    rows = yfinance.YFinanceProvider().get_prices("BRKB", "2026-01-01", "2026-01-31")
    assert len(rows) == 1
    assert calls == ["BRKB", "BRK.B"]  # fell through to the dotted variant


# --------------------------------------------------------------------------
# get_ratios / get_classification
# --------------------------------------------------------------------------

def test_yf_get_ratios(monkeypatch):
    info = {"trailingPE": 20.0, "marketCap": 1000, "returnOnEquity": 0.15,
            "sharesOutstanding": 100}

    class FakeTicker:
        @property
        def info(self):
            return info

    monkeypatch.setattr(yfinance.yf, "Ticker", lambda t: FakeTicker())
    r = yfinance.YFinanceProvider().get_ratios("AAPL")
    assert r["trailing_pe"] == 20.0
    assert r["market_cap"] == 1000
    assert r["roe"] == 0.15
    assert r["shares_outstanding"] == 100
    assert r["forward_pe"] is None


def test_yf_get_ratios_error(monkeypatch):
    def boom(t):
        raise RuntimeError("x")

    monkeypatch.setattr(yfinance.yf, "Ticker", boom)
    assert yfinance.YFinanceProvider().get_ratios("AAPL") == {}


def test_yf_get_classification(monkeypatch):
    class FakeTicker:
        @property
        def info(self):
            return {"sector": "Technology", "industry": "Consumer Electronics"}

    monkeypatch.setattr(yfinance.yf, "Ticker", lambda t: FakeTicker())
    assert yfinance.YFinanceProvider().get_classification("AAPL") == {
        "sector": "Technology", "industry": "Consumer Electronics"}


def test_yf_get_classification_error(monkeypatch):
    def boom(t):
        raise RuntimeError("x")

    monkeypatch.setattr(yfinance.yf, "Ticker", boom)
    assert yfinance.YFinanceProvider().get_classification("AAPL") == {
        "sector": None, "industry": None}


# --------------------------------------------------------------------------
# get_fundamentals
# --------------------------------------------------------------------------

def test_yf_get_fundamentals(monkeypatch):
    inc, bal, cf = _fs_statements()

    class FakeTicker:
        income_stmt = inc
        balance_sheet = bal
        cashflow = cf

    monkeypatch.setattr(yfinance.yf, "Ticker", lambda t: FakeTicker())
    rows = yfinance.YFinanceProvider().get_fundamentals("AAPL")
    assert len(rows) == 2
    assert rows[0]["fiscal_year"] == 2025
    assert rows[0]["f_score"] == 8
