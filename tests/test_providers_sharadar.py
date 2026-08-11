"""Tests for the Sharadar provider (FMP replacement).

All fixtures are synthetic CVS rows in the exact Sharadar /v1.0/data/<table>
shape (verified live 2026-08-11 with the test key: sf1 ARY fields, stocks
open/high/low/close + closeadj, tickers master). No network.
"""
import pytest

from data_manager.providers import sharadar as S

CSV = "date,open,high,low,close,closeunadj,ticker,volume,closeadj,lastupdated\n" \
      "2026-02-01,11,12,10,11.5,11.5,AA,3000,10.35,\n" \
      "2026-01-03,10.5,12,10,11,11,AA,2000,9.9,\n" \
      "2026-01-02,10,11,9,10.5,10.5,AA,1000,9.45,\n"


def _rows(csv_text):
    import csv, io
    return list(csv.DictReader(io.StringIO(csv_text)))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def test_num_nullish():
    assert S._num(None) is None
    assert S._num("") is None
    assert S._num("N/A") is None
    assert S._num("12.5") == 12.5
    assert S._num("garbage") is None


def test_gics_mapping():
    assert S.gics_sector("Technology") == "Information Technology"
    assert S.gics_sector("Healthcare") == "Health Care"
    assert S.gics_sector("Finance") == "Financials"
    assert S.gics_sector("Consumer Non-Durables") == "Consumer Staples"
    assert S.gics_sector("Consumer Durables") == "Consumer Discretionary"
    assert S.gics_sector("Producer Manufacturing") == "Industrials"
    assert S.gics_sector("Process Industries") == "Materials"
    assert S.gics_sector("Utilities") == "Utilities"
    assert S.gics_sector("Communications") == "Communication Services"
    assert S.gics_sector("Real Estate") == "Real Estate"
    assert S.gics_sector("Something Exotic") is None   # fails closed
    assert S.gics_sector(None) is None


# --------------------------------------------------------------------------
# prices
# --------------------------------------------------------------------------

def test_get_prices_shape_and_adjustment(monkeypatch):
    monkeypatch.setattr(S, "_fetch", lambda table, **p: _rows(CSV))
    rows = S.SharadarProvider().get_prices("AA", "2026-01-01", "2026-01-31")
    assert [r["date"] for r in rows] == ["2026-01-02", "2026-01-03"]
    assert rows[0]["close"] == 10.5
    assert rows[0]["adjustment"] == pytest.approx(9.45 / 10.5)
    assert rows[1]["adjustment"] == pytest.approx(9.9 / 11)
    assert rows[1]["volume"] == 2000


def test_price_table_routing_via_master(monkeypatch):
    master = [{"table": "funds", "ticker": "VXX"}]
    def fake_fetch(table, **p):
        return master if table == "tickers" else []
    monkeypatch.setattr(S, "_fetch", fake_fetch)
    p = S.SharadarProvider()
    assert p._price_table("VXX") == "funds"


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def test_classification_maps_sector(monkeypatch):
    master = [{"ticker": "AAPL", "sector": "Technology",
               "industry": "Consumer Electronics"}]
    monkeypatch.setattr(S, "_fetch", lambda table, **p: master)
    c = S.SharadarProvider().get_classification("AAPL")
    assert c == {"sector": "Information Technology",
                 "industry": "Consumer Electronics"}


def test_classification_unmapped_sector_fails_closed(monkeypatch):
    monkeypatch.setattr(S, "_fetch", lambda table, **p: [{"ticker": "X",
                                                          "sector": "Weird", "industry": "Y"}])
    assert S.SharadarProvider().get_classification("X")["sector"] is None


# --------------------------------------------------------------------------
# fundamentals: F-score must match the FMP signal math exactly
# --------------------------------------------------------------------------

def test_fundamentals_fscore_matches_fmp_math(monkeypatch):
    # two fiscal years, engineered so every signal flips
    rows = [
        {"calendardate": "2024-12-31", "netinc": "100", "ncfo": "120",
         "assets": "800", "liabilities": "700", "assetsc": "400",
         "liabilitiesc": "300", "shareswa": "100", "gp": "300", "revenue": "1000"},
        {"calendardate": "2023-12-31", "netinc": "50", "ncfo": "40",
         "assets": "800", "liabilities": "750", "assetsc": "350",
         "liabilitiesc": "400", "shareswa": "110", "gp": "200", "revenue": "900"},
    ]
    monkeypatch.setattr(S, "_fetch", lambda table, **p: rows)
    out = S.SharadarProvider().get_fundamentals("X")
    y24 = [r for r in out if r["fiscal_year"] == 2024][0]
    # 2024 vs 2023: roa up, cfo>ni (accrual ok), leverage down, liquidity up,
    # shares down (no issuance), gm up, at up -> 7 signals + cfo_pos = 8 (at 1.25 > 1.125)
    assert y24["d_roa"] == 1
    assert y24["accruals"] == 1
    assert y24["d_leverage"] == 1
    assert y24["d_liquidity"] == 1
    assert y24["equity_issuance"] == 1
    assert y24["d_gross_margin"] == 1
    assert y24["d_asset_turnover"] == 1
    assert y24["f_score"] == 8
    assert out[0]["fiscal_year"] == 2023  # sorted ascending


# --------------------------------------------------------------------------
# quarterly + ratios
# --------------------------------------------------------------------------

def test_quarterly_shape(monkeypatch):
    rows = [{"reportperiod": "2024-03-31", "netinc": "10", "revenue": "50",
             "gp": "20", "ncfo": "15", "assets": "500", "liabilities": "300",
             "assetsc": "200", "liabilitiesc": "150", "shareswa": "90"}]
    monkeypatch.setattr(S, "_fetch", lambda table, **p: rows)
    q = S.SharadarProvider().get_quarterly("X")[0]
    assert q["period"] == "2024-03-31"
    assert q["net_income"] == 10 and q["roa"] == pytest.approx(10 / 500)


def test_ratios_shape(monkeypatch):
    rows = [{"calendardate": "2024-12-31", "pe": "20", "pb": "3", "ps": "4",
             "roe": "0.2", "roa": "0.1", "netmargin": "0.15", "grossmargin": "0.4",
             "opinc": "30", "revenue": "100", "de": "1.5", "currentratio": "2",
             "divyield": "0.01", "marketcap": "1e9", "ev": "1.2e9",
             "evebitda": "9", "shareswa": "1e8"}]
    monkeypatch.setattr(S, "_fetch", lambda table, **p: rows)
    r = S.SharadarProvider().get_ratios("X")
    assert r["trailing_pe"] == 20
    assert r["operating_margin"] == pytest.approx(0.3)
    assert r["ev_to_ebitda"] == 9
    assert r["shares_outstanding"] == 1e8
    assert r["forward_pe"] is None and r["beta"] is None
