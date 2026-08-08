"""Tests for data_manager.providers.financialdatasets — universe pagination + dedup (mocked)."""

import pytest

from data_manager.providers.financialdatasets import (
    API_BASE,
    FinancialDatasetsProvider,
    R1000_ETF,
    R2000_ETF,
)


class FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("FINDAT", raising=False)
    with pytest.raises(RuntimeError):
        FinancialDatasetsProvider(api_key=None)


def test_constructor_accepts_key():
    prov = FinancialDatasetsProvider(api_key="k")
    assert prov.headers == {"X-API-KEY": "k"}


def test_get_universe_dedupes_across_etfs(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        etf = params["ticker"]
        offset = params["offset"]
        calls.append((etf, offset))
        if offset == 0:
            return FakeResp({
                "holdings": [{"ticker": "AAPL", "name": "Apple"}],
                "fund": {"returned": 1},
                "next_page_url": "/next",
            })
        return FakeResp({
            "holdings": [{"ticker": "GOOG", "name": "Alphabet"}],
            "fund": {"returned": 1},
            "next_page_url": None,
        })

    monkeypatch.setattr("data_manager.providers.financialdatasets.httpx.get", fake_get)
    prov = FinancialDatasetsProvider(api_key="k")
    rows = prov.get_universe()
    assert {r["ticker"] for r in rows} == {"AAPL", "GOOG"}
    # 2 ETFs x 2 pages
    assert len(calls) == 4
    assert calls[0][0] == R1000_ETF
    assert calls[2][0] == R2000_ETF
    assert [c[1] for c in calls] == [0, 1, 0, 1]  # offset pagination


def test_get_all_holdings_stops_on_empty_page(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResp({"holdings": [], "fund": {"returned": 0},
                         "next_page_url": None})

    monkeypatch.setattr("data_manager.providers.financialdatasets.httpx.get", fake_get)
    prov = FinancialDatasetsProvider(api_key="k")
    assert prov._get_all_holdings("IWB") == []


def test_get_all_holdings_uses_api_base(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen["url"] = url
        return FakeResp({"holdings": [], "fund": {"returned": 0},
                         "next_page_url": None})

    monkeypatch.setattr("data_manager.providers.financialdatasets.httpx.get", fake_get)
    FinancialDatasetsProvider(api_key="k")._get_all_holdings("IWB")
    assert seen["url"] == f"{API_BASE}/index-funds"
