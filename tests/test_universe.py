"""Tests for data_manager.universe — orchestration over providers + SQLite."""

import datetime as dt

import pytest

from data_manager import universe
from data_manager.providers.base import BaseProvider
from data_manager.providers.sharadar import SharadarProvider


class FakeProvider(BaseProvider):
    """In-memory provider with scriptable responses and call tracking."""

    name = "fake"

    def __init__(self, universe=None, prices=None, classification=None,
                 fundamentals=None, ratios=None, quarterly=None, as_of=None,
                 raise_on=None):
        self._universe = universe or []
        self._prices = prices or []
        self._classification = classification or {}
        self._fundamentals = fundamentals or []
        self._ratios = ratios or {}
        self._quarterly = quarterly or []
        self._as_of = as_of
        self.raise_on = set(raise_on or [])
        self.calls = []

    def get_universe(self):
        self.calls.append("get_universe")
        return self._universe

    def get_as_of_date(self):
        return self._as_of

    def get_prices(self, ticker, start, end):
        self.calls.append(("get_prices", ticker))
        if "get_prices" in self.raise_on:
            raise RuntimeError("boom")
        return self._prices

    def get_classification(self, ticker):
        self.calls.append(("get_classification", ticker))
        if "get_classification" in self.raise_on:
            raise RuntimeError("boom")
        return self._classification

    def get_fundamentals(self, ticker):
        self.calls.append(("get_fundamentals", ticker))
        if "get_fundamentals" in self.raise_on:
            raise RuntimeError("boom")
        return self._fundamentals

    def get_ratios(self, ticker):
        self.calls.append(("get_ratios", ticker))
        if "get_ratios" in self.raise_on:
            raise RuntimeError("boom")
        return self._ratios

    def get_quarterly(self, ticker):
        self.calls.append(("get_quarterly", ticker))
        if "get_quarterly" in self.raise_on:
            raise RuntimeError("boom")
        return self._quarterly


# --------------------------------------------------------------------------
# update_universe
# --------------------------------------------------------------------------

def test_update_universe_stores_rows(conn):
    prov = FakeProvider(universe=[
        {"ticker": "AAPL", "name": "Apple", "source": "IWV"},
        {"ticker": "MSFT", "name": "Microsoft", "source": "IWV"},
    ])
    n = universe.update_universe(conn=conn, provider=prov)
    assert n == 2
    rows = conn.execute(
        "SELECT ticker, name, source FROM universe ORDER BY ticker").fetchall()
    assert [r["ticker"] for r in rows] == ["AAPL", "MSFT"]
    assert rows[0]["name"] == "Apple"
    assert rows[0]["source"] == "IWV"


def test_update_universe_records_snapshot(conn):
    prov = FakeProvider(universe=[
        {"ticker": "AAPL", "name": "Apple", "source": "IWV"}], as_of="2026-08-06")
    universe.update_universe(conn=conn, provider=prov)
    sn = conn.execute("SELECT source, as_of, row_count FROM snapshots").fetchone()
    assert sn["source"] == "fake"
    assert sn["as_of"] == "2026-08-06"
    assert sn["row_count"] == 1


def test_update_universe_stores_classifications_when_sector_present(conn):
    prov = FakeProvider(universe=[
        {"ticker": "AAPL", "name": "Apple", "source": "IWV", "sector": "Technology"},
        {"ticker": "MSFT", "name": "Microsoft", "source": "IWV", "sector": "Technology"},
    ])
    universe.update_universe(conn=conn, provider=prov)
    rows = conn.execute(
        "SELECT ticker, sector, industry, as_of FROM classifications ORDER BY ticker").fetchall()
    assert len(rows) == 2
    assert rows[0]["sector"] == "Technology"
    assert rows[0]["industry"] is None
    assert rows[0]["as_of"] == dt.date.today().isoformat()


def test_update_universe_skips_classifications_without_sector(conn):
    prov = FakeProvider(universe=[
        {"ticker": "AAPL", "name": "Apple", "source": "IWV", "sector": None}])
    universe.update_universe(conn=conn, provider=prov)
    n = conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
    assert n == 0


# --------------------------------------------------------------------------
# update_prices
# --------------------------------------------------------------------------

PRICE_ROW = {"date": "2026-01-02", "open": 1.0, "high": 2.0, "low": 0.5,
             "close": 1.5, "volume": 100, "adjustment": 1.0}


def test_update_prices_stores_rows(conn):
    prov = FakeProvider(prices=[PRICE_ROW])
    n = universe.update_prices(["AAPL"], "2026-01-01", "2026-01-31",
                               conn=conn, provider=prov, pace=0)
    assert n == 1
    row = conn.execute("SELECT * FROM prices").fetchone()
    assert row["ticker"] == "AAPL"
    assert row["date"] == "2026-01-02"
    assert row["close"] == 1.5


def test_update_prices_resumable_skips_covered(conn):
    conn.execute(
        "INSERT INTO prices (ticker, date, open, high, low, close, volume, adjustment) "
        "VALUES ('AAPL', '2026-01-31', 1, 2, 0.5, 1.5, 100, 1.0)")
    conn.commit()
    prov = FakeProvider(prices=[PRICE_ROW])
    n = universe.update_prices(["AAPL"], "2026-01-01", "2026-01-31",
                               conn=conn, provider=prov, pace=0)
    assert n == 0
    assert prov.calls == []  # provider never touched


def test_update_prices_handles_provider_error(conn, capsys, monkeypatch):
    monkeypatch.setattr(universe.time, "sleep", lambda s: None)
    prov = FakeProvider(prices=[], raise_on={"get_prices"})
    n = universe.update_prices(["AAPL"], "2026-01-01", "2026-01-31",
                               conn=conn, provider=prov, pace=0)
    assert n == 0
    out = capsys.readouterr().out
    assert "AAPL" in out and "RuntimeError" in out


# --------------------------------------------------------------------------
# update_classifications
# --------------------------------------------------------------------------

def test_update_classifications_stores(conn):
    prov = FakeProvider(classification={"sector": "Tech", "industry": "Software"})
    n = universe.update_classifications(["AAPL"], conn=conn, provider=prov)
    assert n == 1
    row = conn.execute(
        "SELECT * FROM classifications WHERE ticker='AAPL'").fetchone()
    assert row["sector"] == "Tech"
    assert row["industry"] == "Software"
    assert row["as_of"] == dt.date.today().isoformat()


def test_update_classifications_resumable(conn):
    conn.execute(
        "INSERT INTO classifications (ticker, sector, industry, as_of) "
        "VALUES ('AAPL', 'Tech', 'Software', '2026-01-01')")
    conn.commit()
    prov = FakeProvider(classification={"sector": "X", "industry": "Y"})
    n = universe.update_classifications(["AAPL"], conn=conn, provider=prov)
    assert n == 0
    assert prov.calls == []


def test_update_classifications_handles_error(conn, capsys, monkeypatch):
    monkeypatch.setattr(universe.time, "sleep", lambda s: None)
    prov = FakeProvider(classification={}, raise_on={"get_classification"})
    n = universe.update_classifications(["AAPL"], conn=conn, provider=prov)
    assert n == 0


# --------------------------------------------------------------------------
# update_fundamentals
# --------------------------------------------------------------------------

FUND_ROW = {"fiscal_year": 2024, "roa": 0.1, "cfo": 1.0, "d_roa": 1,
            "accruals": 0, "d_leverage": 1, "d_liquidity": 0,
            "equity_issuance": 1, "d_gross_margin": 0,
            "d_asset_turnover": 1, "f_score": 5}


def test_update_fundamentals_stores(conn):
    prov = FakeProvider(fundamentals=[FUND_ROW])
    n = universe.update_fundamentals(["AAPL"], conn=conn, provider=prov, pace=0)
    assert n == 1
    row = conn.execute(
        "SELECT * FROM fundamentals WHERE ticker='AAPL'").fetchone()
    assert row["fiscal_year"] == 2024
    assert row["f_score"] == 5
    assert row["roa"] == 0.1


def test_update_fundamentals_resumable(conn):
    conn.execute(
        "INSERT INTO fundamentals (ticker, fiscal_year, roa, cfo, f_score) "
        "VALUES ('AAPL', 2024, 0.1, 1.0, 5)")
    conn.commit()
    prov = FakeProvider(fundamentals=[FUND_ROW])
    n = universe.update_fundamentals(["AAPL"], conn=conn, provider=prov, pace=0)
    assert n == 0
    assert prov.calls == []


def test_update_fundamentals_handles_error(conn, capsys, monkeypatch):
    monkeypatch.setattr(universe.time, "sleep", lambda s: None)
    prov = FakeProvider(fundamentals=[], raise_on={"get_fundamentals"})
    n = universe.update_fundamentals(["AAPL"], conn=conn, provider=prov, pace=0)
    assert n == 0


# --------------------------------------------------------------------------
# adjusted_prices
# --------------------------------------------------------------------------

def _insert_price(conn, ticker, date, close, adjustment=None):
    conn.execute(
        "INSERT INTO prices (ticker, date, open, high, low, close, volume, adjustment) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ticker, date, close, close, close, close, 100, adjustment))
    conn.commit()


def test_adjusted_prices_applies_adjustment(conn):
    _insert_price(conn, "AAPL", "2026-01-02", 10.0, 0.5)
    rows = universe.adjusted_prices("AAPL", conn=conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["adjustment"] == 0.5
    assert r["adjusted_open"] == 5.0
    assert r["adjusted_high"] == 5.0
    assert r["adjusted_low"] == 5.0
    assert r["adjusted_close"] == 5.0
    assert r["volume"] == 100


def test_adjusted_prices_defaults_adjustment_to_one(conn):
    _insert_price(conn, "AAPL", "2026-01-02", 10.0, None)
    r = universe.adjusted_prices("AAPL", conn=conn)[0]
    assert r["adjustment"] == 1.0
    assert r["adjusted_close"] == 10.0


def test_adjusted_prices_rebases_to_asof(conn):
    # 2:1 split on 2026-01-05 (0.5 before, 1.0 after), 10:1 split on
    # 2026-01-06 (0.1 after): today-anchored factor = 0.1.
    _insert_price(conn, "AAPL", "2026-01-02", 100.0, 0.5)
    _insert_price(conn, "AAPL", "2026-01-05", 52.0, 1.0)
    _insert_price(conn, "AAPL", "2026-01-06", 5.2, 0.1)
    rows = universe.adjusted_prices("AAPL", asof="2026-01-06", conn=conn)
    now = universe.adjusted_prices("AAPL", conn=conn)
    # rebased to the as-of date: that row equals its as-traded price
    assert rows[2]["adjusted_close"] == 5.2
    # levels: rebased pre-split row = raw * 0.5 / 0.1 (both splits known by
    # the as-of date), stored level = raw * 0.5 (anchored to today instead)
    assert rows[0]["adjusted_close"] == 500.0
    assert abs(rows[0]["adjusted_close"] - now[0]["adjusted_close"]) > 1e-9
    # rebase is a pure scale: returns across any two rows are identical
    def ret(rs, i, j):
        return rs[j]["adjusted_close"] / rs[i]["adjusted_close"]
    assert abs(ret(rows, 0, 2) - ret(now, 0, 2)) < 1e-12
    assert abs(ret(rows, 1, 2) - ret(now, 1, 2)) < 1e-12


def test_adjusted_prices_asof_requires_price_by_that_date(conn):
    _insert_price(conn, "AAPL", "2026-02-01", 10.0, 1.0)
    with pytest.raises(ValueError, match="no adjustment on or before"):
        universe.adjusted_prices("AAPL", asof="2026-01-01", conn=conn)


def test_adjusted_prices_filters_and_orders(conn):
    for d, c in [("2026-01-01", 1.0), ("2026-01-02", 2.0), ("2026-01-03", 3.0)]:
        _insert_price(conn, "AAPL", d, c, 1.0)
    rows = universe.adjusted_prices("AAPL", start="2026-01-02",
                                    end="2026-01-02", conn=conn)
    assert [r["date"] for r in rows] == ["2026-01-02"]
    rows = universe.adjusted_prices("AAPL", conn=conn)
    assert [r["date"] for r in rows] == ["2026-01-01", "2026-01-02", "2026-01-03"]


# --------------------------------------------------------------------------
# update_ratios
# --------------------------------------------------------------------------

def test_update_ratios_stores_snapshot(conn):
    prov = FakeProvider(ratios={"trailing_pe": 20.0, "roe": 0.15})
    n = universe.update_ratios(["AAPL"], conn=conn, provider=prov, pace=0)
    assert n == 1
    row = conn.execute("SELECT * FROM ratios WHERE ticker='AAPL'").fetchone()
    assert row["as_of"] == dt.date.today().isoformat()
    assert row["trailing_pe"] == 20.0
    assert row["roe"] == 0.15


def test_update_ratios_resumable_today(conn):
    today = dt.date.today().isoformat()
    conn.execute(
        "INSERT INTO ratios (ticker, as_of, trailing_pe) VALUES ('AAPL', ?, 20.0)",
        (today,))
    conn.commit()
    prov = FakeProvider(ratios={"trailing_pe": 99.0})
    n = universe.update_ratios(["AAPL"], conn=conn, provider=prov, pace=0)
    assert n == 0
    assert prov.calls == []


def test_update_ratios_skips_empty(conn):
    prov = FakeProvider(ratios={})
    n = universe.update_ratios(["AAPL"], conn=conn, provider=prov, pace=0)
    assert n == 0


# --------------------------------------------------------------------------
# update_quarterly
# --------------------------------------------------------------------------

def test_update_quarterly_stores(conn):
    prov = FakeProvider(quarterly=[
        {"period": "2026-03-31", "net_income": 100.0, "revenue": 1000.0}])
    n = universe.update_quarterly(["AAPL"], conn=conn, provider=prov, pace=0)
    assert n == 1
    row = conn.execute(
        "SELECT * FROM quarterly_statements WHERE ticker='AAPL'").fetchone()
    assert row["period"] == "2026-03-31"
    assert row["net_income"] == 100.0


def test_update_quarterly_resumable(conn):
    conn.execute(
        "INSERT INTO quarterly_statements (ticker, period, net_income) "
        "VALUES ('AAPL', '2026-03-31', 100.0)")
    conn.commit()
    prov = FakeProvider(quarterly=[
        {"period": "2026-03-31", "net_income": 100.0, "revenue": 1000.0}])
    n = universe.update_quarterly(["AAPL"], conn=conn, provider=prov, pace=0)
    assert n == 0
    assert prov.calls == []


# --------------------------------------------------------------------------
# universe_tickers / default provider
# --------------------------------------------------------------------------

def test_universe_tickers_sorted(conn):
    for t in ["MSFT", "AAPL", "GOOG"]:
        conn.execute(
            "INSERT OR REPLACE INTO universe (ticker, name, source, added_at) "
            "VALUES (?, ?, ?, ?)", (t, t, "IWV", "2026-01-01"))
    conn.commit()
    assert universe.universe_tickers(conn=conn) == ["AAPL", "GOOG", "MSFT"]


def test_default_data_provider_is_sharadar():
    assert isinstance(universe._default_data_provider(), SharadarProvider)
