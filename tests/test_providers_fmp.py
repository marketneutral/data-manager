"""Tests for data_manager.providers.fmp — helpers + provider methods (mocked _get)."""

import pytest

from data_manager.providers import fmp


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def test_num():
    assert fmp._num(None) is None
    assert fmp._num("12.5") == 12.5
    assert fmp._num(7) == 7.0
    assert fmp._num(float("nan")) is None
    assert fmp._num("garbage") is None
    assert fmp._num([]) is None


def test_safe_div():
    assert fmp._safe_div(10, 2) == 5.0
    assert fmp._safe_div(10, 0) is None
    assert fmp._safe_div(None, 2) is None
    assert fmp._safe_div(10, None) is None
    assert fmp._safe_div("a", 2) is None


def test_key_from_env(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "abc")
    assert fmp._key() == "abc"


def test_key_falls_back_to_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("FMP_API_KEY=filekey\nOTHER=1\n")
    monkeypatch.setattr(fmp.os.path, "expanduser", lambda p: str(env))
    monkeypatch.setattr(fmp.os.path, "exists", lambda p: p == str(env))
    assert fmp._key() == "filekey"


def test_key_missing(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setattr(fmp.os.path, "exists", lambda p: False)
    assert fmp._key() == ""


# --------------------------------------------------------------------------
# get_prices
# --------------------------------------------------------------------------

def test_fmp_get_prices_filters_and_adjusts(monkeypatch):
    # Non-split-adjusted EOD (as-traded) is newest-first and mislabels raw fields
    # as adjOpen/adjHigh/adjLow/adjClose; the split-adjusted full history provides
    # adjClose for the total-return factor. The provider merges both and sorts asc.
    raw = [
        {"date": "2026-02-01", "adjOpen": 11, "adjHigh": 12, "adjLow": 10,
         "adjClose": 11.5, "volume": 3000},                       # outside window
        {"date": "2026-01-03", "adjOpen": 10.5, "adjHigh": 12, "adjLow": 10,
         "adjClose": 11, "volume": 2000},                          # as-traded close
        {"date": "2026-01-02", "adjOpen": 10, "adjHigh": 11, "adjLow": 9,
         "adjClose": 10.5, "volume": 1000},
    ]
    full = {"historical": [
        {"date": "2026-02-01", "close": 11.5, "adjClose": 10.35, "volume": 3000},
        {"date": "2026-01-03", "close": 11, "adjClose": 9.9, "volume": 2000},
        {"date": "2026-01-02", "close": 10.5, "adjClose": 9.45, "volume": 1000},
    ]}
    monkeypatch.setattr(fmp, "_get_stable", lambda path, **p: raw)
    monkeypatch.setattr(fmp, "_get", lambda path, **p: full)
    rows = fmp.FMPProvider().get_prices("AAPL", "2026-01-01", "2026-01-31")
    assert [r["date"] for r in rows] == ["2026-01-02", "2026-01-03"]
    assert rows[0]["close"] == 10.5
    assert rows[0]["adjustment"] == pytest.approx(9.45 / 10.5)
    assert rows[1]["adjustment"] == pytest.approx(9.9 / 11)


def test_fmp_get_prices_empty(monkeypatch):
    monkeypatch.setattr(fmp, "_get_stable", lambda path, **p: [])
    assert fmp.FMPProvider().get_prices("AAPL", "2026-01-01", "2026-01-31") == []


# --------------------------------------------------------------------------
# get_classification
# --------------------------------------------------------------------------

def test_fmp_get_classification(monkeypatch):
    monkeypatch.setattr(
        fmp, "_get",
        lambda path, **p: [{"sector": "Technology", "industry": "Consumer Electronics"}])
    assert fmp.FMPProvider().get_classification("AAPL") == {
        "sector": "Technology", "industry": "Consumer Electronics"}


def test_fmp_get_classification_empty(monkeypatch):
    monkeypatch.setattr(fmp, "_get", lambda path, **p: [])
    assert fmp.FMPProvider().get_classification("AAPL") == {}


# --------------------------------------------------------------------------
# get_fundamentals (Piotroski F-Score)
# --------------------------------------------------------------------------

def _fmp_statements():
    inc = [
        {"date": "2025-12-31", "netIncome": 100, "revenue": 1000,
         "grossProfit": 400, "weightedAverageShsOutDil": 100},
        {"date": "2024-12-31", "netIncome": 80, "revenue": 800,
         "grossProfit": 300, "weightedAverageShsOutDil": 100},
    ]
    bal = [
        {"date": "2025-12-31", "totalAssets": 1000, "totalLiabilities": 500,
         "totalCurrentAssets": 300, "totalCurrentLiabilities": 200},
        {"date": "2024-12-31", "totalAssets": 900, "totalLiabilities": 600,
         "totalCurrentAssets": 250, "totalCurrentLiabilities": 250},
    ]
    cas = [
        {"date": "2025-12-31", "operatingCashFlow": 150},
        {"date": "2024-12-31", "operatingCashFlow": 90},
    ]

    def fake_get(path, **p):
        if path.startswith("income-statement"):
            return inc
        if path.startswith("balance-sheet-statement"):
            return bal
        if path.startswith("cash-flow-statement"):
            return cas
        return []

    return fake_get


def test_fmp_get_fundamentals_computes_fscore(monkeypatch):
    monkeypatch.setattr(fmp, "_get", _fmp_statements())
    rows = fmp.FMPProvider().get_fundamentals("AAPL")
    assert len(rows) == 2
    row = rows[0]  # fiscal 2025
    assert row["fiscal_year"] == 2025
    assert row["roa"] == pytest.approx(0.1)
    # All 8 signals fire for this improving company -> F = 8
    assert row["f_score"] == 8
    assert row["d_roa"] == 1
    assert row["accruals"] == 1
    assert row["d_leverage"] == 1
    assert row["d_liquidity"] == 1
    assert row["equity_issuance"] == 1
    assert row["d_gross_margin"] == 1
    assert row["d_asset_turnover"] == 1
    # Prior year row exists too
    assert rows[1]["fiscal_year"] == 2024


def test_fmp_get_fundamentals_skips_empty_year(monkeypatch):
    def fake_get(path, **p):
        if path.startswith("income-statement"):
            return [{"date": "2025-12-31", "netIncome": None, "revenue": None}]
        return []

    monkeypatch.setattr(fmp, "_get", fake_get)
    assert fmp.FMPProvider().get_fundamentals("AAPL") == []


# --------------------------------------------------------------------------
# get_quarterly
# --------------------------------------------------------------------------

def test_fmp_get_quarterly(monkeypatch):
    inc = [{"date": "2026-03-31", "netIncome": 50, "revenue": 500,
            "grossProfit": 200, "weightedAverageShsOutDil": 100}]
    bal = [{"date": "2026-03-31", "totalAssets": 1000, "totalLiabilities": 400,
            "totalCurrentAssets": 200, "totalCurrentLiabilities": 100}]
    cas = [{"date": "2026-03-31", "operatingCashFlow": 60}]

    def fake_get(path, **p):
        if path.startswith("income-statement"):
            return inc
        if path.startswith("balance-sheet-statement"):
            return bal
        if path.startswith("cash-flow-statement"):
            return cas
        return []

    monkeypatch.setattr(fmp, "_get", fake_get)
    rows = fmp.FMPProvider().get_quarterly("AAPL")
    assert len(rows) == 1
    r = rows[0]
    assert r["period"] == "2026-03-31"
    assert r["net_income"] == 50
    assert r["operating_cash_flow"] == 60
    assert r["roa"] == pytest.approx(0.05)


# --------------------------------------------------------------------------
# get_ratios
# --------------------------------------------------------------------------

def test_fmp_get_ratios(monkeypatch):
    def fake_get(path, **p):
        if path.startswith("ratios-ttm"):
            return [{"priceEarningsRatioTTM": 20.0, "priceToBookRatioTTM": 3.0}]
        if path.startswith("key-metrics-ttm"):
            return [{"roeTTM": 0.15, "priceToSalesRatioTTM": 5.0}]
        if path.startswith("profile"):
            return [{"marketCap": 1000, "beta": 1.2}]
        return []

    monkeypatch.setattr(fmp, "_get", fake_get)
    r = fmp.FMPProvider().get_ratios("AAPL")
    assert r["trailing_pe"] == 20.0
    assert r["price_to_book"] == 3.0
    assert r["roe"] == 0.15
    assert r["price_to_sales"] == 5.0
    assert r["market_cap"] == 1000
    assert r["beta"] == 1.2
    assert r["forward_pe"] is None


def test_fmp_get_prices_tries_dash_variant(monkeypatch):
    calls = []

    def fake_stable(path, **p):
        calls.append(p["symbol"])
        return [] if p["symbol"] == "BRKB" else [
            {"date": "2026-01-02", "adjOpen": 10, "adjHigh": 11, "adjLow": 9,
             "adjClose": 10.5, "volume": 1000}]

    monkeypatch.setattr(fmp, "_get_stable", fake_stable)
    monkeypatch.setattr(fmp, "_get", lambda path, **p: {"historical": [
        {"date": "2026-01-02", "close": 10.5, "adjClose": 10.5, "volume": 1000}]})
    rows = fmp.FMPProvider().get_prices("BRKB", "2026-01-01", "2026-01-31")
    assert calls == ["BRKB", "BRK-B"]
    assert rows[0]["close"] == 10.5
