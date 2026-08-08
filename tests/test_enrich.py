"""Tests for data_manager.enrich — FIGI/CIK/SIC/LEI enrichment (all mocked, offline)."""

import pytest

from data_manager import enrich


class FakeResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# --------------------------------------------------------------------------
# _openfigi_with_retry
# --------------------------------------------------------------------------

def test_openfigi_with_retry_success(monkeypatch):
    resp = FakeResponse([{"data": [{"figi": "BBG000B9XRY4"}]}])
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return resp

    monkeypatch.setattr(enrich.httpx, "post", fake_post)
    out = enrich._openfigi_with_retry(
        [{"idType": "TICKER", "idValue": "AAPL"}], min_interval=0)
    assert out is resp
    assert calls[0][0] == enrich.OPENFIGI_MAP
    assert calls[0][1][0]["idValue"] == "AAPL"


def test_openfigi_with_retry_backs_off_then_succeeds(monkeypatch):
    responses = [FakeResponse(status_code=429), FakeResponse(status_code=429),
                 FakeResponse([{}])]
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(url)
        return responses[len(calls) - 1]

    monkeypatch.setattr(enrich.httpx, "post", fake_post)
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
    out = enrich._openfigi_with_retry([{}], min_interval=0)
    assert out.status_code == 200
    assert len(calls) == 3


def test_openfigi_with_retry_gives_up(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return FakeResponse(status_code=503)

    monkeypatch.setattr(enrich.httpx, "post", fake_post)
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        enrich._openfigi_with_retry([{}], attempts=3, min_interval=0)


# --------------------------------------------------------------------------
# enrich_figi
# --------------------------------------------------------------------------

def test_enrich_figi_updates_missing_only(conn, monkeypatch):
    conn.execute(
        "INSERT INTO universe (ticker, name, source, added_at) "
        "VALUES ('AAPL', 'Apple', 'IWV', '2026-01-01')")
    conn.execute(
        "INSERT INTO universe (ticker, name, source, added_at, figi) "
        "VALUES ('MSFT', 'Microsoft', 'IWV', '2026-01-01', 'BBG000BPHFS9')")
    conn.commit()

    def fake_openfigi(body, min_interval=3.0):
        return FakeResponse([{"data": [{"figi": "BBG000B9XRY4"}]}] * len(body))

    monkeypatch.setattr(enrich, "_openfigi_with_retry", fake_openfigi)
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)

    n = enrich.enrich_figi(conn, min_interval=0)
    assert n == 1  # only AAPL (missing figi) enriched
    figi = conn.execute(
        "SELECT figi FROM universe WHERE ticker='AAPL'").fetchone()["figi"]
    assert figi == "BBG000B9XRY4"
    figi = conn.execute(
        "SELECT figi FROM universe WHERE ticker='MSFT'").fetchone()["figi"]
    assert figi == "BBG000BPHFS9"  # untouched


def test_enrich_figi_no_data(conn, monkeypatch):
    conn.execute(
        "INSERT INTO universe (ticker, name, source, added_at) "
        "VALUES ('AAPL', 'Apple', 'IWV', '2026-01-01')")
    conn.commit()
    monkeypatch.setattr(
        enrich, "_openfigi_with_retry",
        lambda body, min_interval=3.0: FakeResponse([{}]))
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
    n = enrich.enrich_figi(conn, min_interval=0)
    assert n == 0
    figi = conn.execute(
        "SELECT figi FROM universe WHERE ticker='AAPL'").fetchone()["figi"]
    assert figi is None


# --------------------------------------------------------------------------
# _cik_map / enrich_cik
# --------------------------------------------------------------------------

def test_cik_map(monkeypatch):
    class FakeResp:
        def json(self):
            return {"0": {"cik_str": "320193", "ticker": "AAPL"},
                    "1": {"cik_str": "789019", "ticker": "msft"}}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(enrich.httpx, "get", lambda *a, **k: FakeResp())
    out = enrich._cik_map()
    assert out == {"AAPL": "0000320193", "MSFT": "0000789019"}


def test_enrich_cik(conn, monkeypatch):
    conn.execute(
        "INSERT INTO universe (ticker, name, source, added_at) "
        "VALUES ('AAPL', 'Apple', 'IWV', '2026-01-01')")
    conn.execute(
        "INSERT INTO universe (ticker, name, source, added_at) "
        "VALUES ('MSFT', 'Microsoft', 'IWV', '2026-01-01')")
    conn.commit()
    monkeypatch.setattr(enrich, "_cik_map", lambda: {"AAPL": "0000320193"})
    n = enrich.enrich_cik(conn)
    assert n == 1
    cik = conn.execute(
        "SELECT cik FROM universe WHERE ticker='AAPL'").fetchone()["cik"]
    assert cik == "0000320193"
    assert conn.execute(
        "SELECT cik FROM universe WHERE ticker='MSFT'").fetchone()["cik"] is None


# --------------------------------------------------------------------------
# enrich_sic_lei
# --------------------------------------------------------------------------

def test_enrich_sic_lei(conn, monkeypatch):
    conn.execute(
        "INSERT INTO universe (ticker, name, source, added_at, cik) "
        "VALUES ('AAPL', 'Apple', 'IWV', '2026-01-01', '0000320193')")
    conn.execute(
        "INSERT INTO universe (ticker, name, source, added_at, cik, sic, sic_description) "
        "VALUES ('MSFT', 'Microsoft', 'IWV', '2026-01-01', '0000789019', "
        "        '3674', 'Electronic Computers')")
    conn.commit()

    class FakeResp:
        status_code = 200

        def json(self):
            return {"sic": "3571", "sicDescription": "Electronic Computers",
                    "lei": "HWUPKR0MPOU8FGXBT394"}

    def fake_get(url, headers=None, timeout=None):
        assert "0000320193" in url  # only AAPL is fetched
        return FakeResp()

    monkeypatch.setattr(enrich.httpx, "get", fake_get)
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)

    n = enrich.enrich_sic_lei(conn)
    assert n == 1
    row = conn.execute(
        "SELECT sic, sic_description, lei FROM universe WHERE ticker='AAPL'").fetchone()
    assert row["sic"] == "3571"
    assert row["sic_description"] == "Electronic Computers"
    assert row["lei"] == "HWUPKR0MPOU8FGXBT394"
    # MSFT already complete -> untouched
    assert conn.execute(
        "SELECT sic FROM universe WHERE ticker='MSFT'").fetchone()["sic"] == "3674"


def test_enrich_sic_lei_respects_max_tickers(conn, monkeypatch):
    for i, t in enumerate(["AAA", "BBB", "CCC"]):
        conn.execute(
            "INSERT INTO universe (ticker, name, source, added_at, cik) "
            "VALUES (?, ?, ?, ?, ?)", (t, t, "IWV", "2026-01-01", f"{i:010d}"))
    conn.commit()

    class FakeResp:
        status_code = 200

        def json(self):
            return {"sic": "1000", "sicDescription": "X", "lei": "L"}

    monkeypatch.setattr(enrich.httpx, "get", lambda *a, **k: FakeResp())
    monkeypatch.setattr(enrich.time, "sleep", lambda s: None)
    n = enrich.enrich_sic_lei(conn, max_tickers=2)
    assert n == 2
