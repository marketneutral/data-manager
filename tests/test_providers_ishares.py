"""Tests for data_manager.providers.ishares — IWV holdings CSV parsing (mocked fetch)."""

from data_manager.providers.ishares import ISharesProvider, IWV_CSV, IWV_PAGE

CSV = """Fund Holdings as of Aug 06, 2026
Ticker,Name,Sector,Asset Class,Weight (%),Price,Shares,Market Value,Notional Value
AAPL,Apple Inc.,Information Technology,Equity,5.5,200.0,1000,200000,200000
MSFT,Microsoft Corp.,Information Technology,Equity,5.0,400.0,500,200000,200000
,Some Non-Ticker Holding,Utilities,Equity,0.1,10.0,10,100,100
"""


def _fake_client():
    """A scripted httpx.Client that returns CSV for the holdings URL."""

    class FakeResp:
        def __init__(self, url):
            self.url = url
            self.text = CSV if url == IWV_CSV else ""

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kw):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            self.calls.append(url)
            return FakeResp(url)

    return FakeClient()


def test_load_fetches_and_caches(monkeypatch):
    client = _fake_client()
    monkeypatch.setattr("data_manager.providers.ishares.httpx.Client",
                        lambda **kw: client)
    prov = ISharesProvider()
    assert prov._load() == CSV
    assert prov._load() == CSV  # cached: no extra network
    assert client.calls == [IWV_PAGE, IWV_CSV]


def test_get_as_of_date(monkeypatch):
    monkeypatch.setattr(ISharesProvider, "_load", lambda self: CSV)
    assert ISharesProvider().get_as_of_date() == "2026-08-06"


def test_get_as_of_date_missing(monkeypatch):
    monkeypatch.setattr(ISharesProvider, "_load", lambda self: "no header here")
    assert ISharesProvider().get_as_of_date() is None


def test_get_universe(monkeypatch):
    monkeypatch.setattr(ISharesProvider, "_load", lambda self: CSV)
    rows = ISharesProvider().get_universe()
    assert len(rows) == 2  # the empty-ticker row is skipped
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["name"] == "Apple Inc."
    assert rows[0]["sector"] == "Information Technology"
    assert rows[0]["source"] == "IWV"


def test_get_universe_empty(monkeypatch):
    monkeypatch.setattr(ISharesProvider, "_load", lambda self: "no header here")
    assert ISharesProvider().get_universe() == []


def test_unimplemented_methods_raise():
    prov = ISharesProvider()
    for meth, args in [
        ("get_prices", ("AAPL", "2026-01-01", "2026-01-31")),
        ("get_classification", ("AAPL",)),
        ("get_fundamentals", ("AAPL",)),
    ]:
        try:
            getattr(prov, meth)(*args)
        except NotImplementedError:
            pass
        else:
            raise AssertionError(f"{meth} should raise NotImplementedError")
