"""Tests for the warehouse-mode pieces (whole-table pulls, batched SF1/prices,
PIT universe construction). All synthetic; no network."""
import sqlite3
import pytest

from data_manager import db
from data_manager.universe import (
    _sf1_batches, _price_batches, build_universe_pit, master_stocks,
    _numf,
)
from data_manager.providers import sharadar as S


def test_sf1_batches_cap_tickers():
    ticks = [f"T{i:04d}" for i in range(1000)]
    for dim in ["ARY", "MRY", "ARQ", "MRQ"]:
        for b in _sf1_batches(ticks, dim):
            assert len(b) <= 25
    assert sum(len(b) for b in _sf1_batches(ticks, "ARY")) == 1000


def test_price_batches_respect_ticker_and_row_caps(conn):
    ticks = [f"T{i:04d}" for i in range(200)]
    # all full-history tickers (est ~7196 rows each)
    for t in ticks:
        conn.execute("INSERT OR REPLACE INTO securities_master (ticker, firstpricedate, lastpricedate, \"table\") VALUES (?, '1998-01-01', '2026-08-11', 'stocks')", (t,))
    conn.commit()
    bs = _price_batches(ticks, "1996-01-01", "2026-08-11", conn)
    for b in bs:
        assert len(b) <= 28
    # total estimated rows per batch stays under ~30k -> no truncation
    assert all(len(b) <= 5 for b in bs), len(bs[0])


def test_parse_prices_carries_ticker_and_as_traded():
    csv_text = "date,open,high,low,close,closeunadj,ticker,volume,closeadj,lastupdated\n" \
               "2016-08-01,26.102,26.538,26.102,26.512,106.05,AA,1000,24.12,\n"
    import csv, io
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    out = S.parse_prices(rows, "2016-08-01", "2016-08-02")
    assert out[0]["ticker"] == "AA"
    assert out[0]["close"] == pytest.approx(106.05)   # as-traded, split-scaled
    assert out[0]["adjustment"] == pytest.approx(24.12 / 106.05)


def test_numf_nullish():
    assert _numf(None) is None and _numf("") is None and _numf("N/A") is None
    assert _numf("12.5") == 12.5


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_build_universe_pit_filters(conn):
    # two candidates: GOOD (passes) and BAD (price < 2)
    for t in ["GOOD", "BAD", "NOQ", "LOWDV"]:
        conn.execute("INSERT INTO securities_master "
                     "(ticker, category, exchange, isdelisted, sector, industry, firstpricedate, lastpricedate, \"table\") "
                     "VALUES (?, 'Domestic Common Stock', 'NASDAQ', 'N', 'Technology', 'SW', '2000-01-01', '2026-08-10', 'stocks')", (t,))
    # prices: GOOD $50 avg dvol $1B; BAD $1 (fails price); NOQ no quotes; LOWDV low volume
    for t, px, vol in [("GOOD", 50.0, 20_000_000), ("BAD", 1.0, 20_000_000), ("LOWDV", 50.0, 1000)]:
        for i in range(25):
            conn.execute("INSERT INTO prices (ticker, date, close, volume, adjustment) VALUES (?, ?, ?, ?, 1.0)",
                         (t, f"2026-07-{i+1:02d}", px, vol))
    # shares for mcap
    for t in ["GOOD", "BAD", "LOWDV"]:
        conn.execute("INSERT INTO sf1 (ticker, dimension, date, reportperiod, shareswa, data) "
                     "VALUES (?, 'ARY', '2025-12-31', '2025-FY', 100000000, '{}')", (t,))
    conn.commit()
    n = build_universe_pit(conn, as_of="2026-08-10", min_price=2.0, min_mcap=100_000_000.0,
                           min_dvol=1_000_000.0, lookback=20, min_dvol_days=10,
                           max_quote_age=30)
    members = {r[0] for r in conn.execute("SELECT ticker FROM universe_pit")}
    assert members == {"GOOD"}
    assert n == 1
    row = conn.execute("SELECT mcap, dvol_avg, dvol_days FROM universe_pit WHERE ticker='GOOD'").fetchone()
    assert row[0] == pytest.approx(50.0 * 100_000_000)
    assert row[2] == 25
