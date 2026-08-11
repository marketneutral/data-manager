"""Tests for data_manager.etfs -- the maintained ETF extras manifest."""

from data_manager.etfs import ETFS, etf_tickers


def test_manifest_keys_are_uppercase_unique():
    keys = list(ETFS)
    assert len(keys) == len(set(keys))
    assert all(k == k.upper() and k.isalpha() and len(k) <= 5 for k in keys)


def test_manifest_has_key_sector_and_benchmark_coverage():
    cats = {m["category"] for m in ETFS.values()}
    assert all(c in {"benchmark", "volatility", "sector", "bonds",
                     "international", "commodities", "fx"} for c in cats)
    n_sector = sum(1 for m in ETFS.values() if m["category"] == "sector")
    assert n_sector >= 8      # full SPDR sector set
    assert any(t == "SPY" for t in ETFS) and any(t == "VXX" for t in ETFS)

def test_etf_tickers_matches_manifest():
    assert etf_tickers() == sorted(ETFS)
