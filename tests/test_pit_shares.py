"""Point-in-time shares and market cap across splits.

SF1 share counts are restated for every split after the filing, so the PIT count on day D
is the restated count divided by the splits after D. A market cap built that way is
continuous across a split, and a rebuild removes members that no longer qualify.
"""

import pytest

from data_manager import db
from data_manager.universe import (build_shares_pit, build_universe_pit,
                                   build_universe_pit_history, pit_share_events)

DAYS = ["2014-06-%02d" % d for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 16, 17)]


def _stock(c, t):
    c.execute('INSERT OR REPLACE INTO securities_master (ticker, category, exchange, isdelisted,'
              ' sector, industry, firstpricedate, lastpricedate, name, permaticker, "table")'
              ' VALUES (?,?,?,?,?,?,?,?,?,?,?)',
              (t, "Domestic Common Stock", "NASDAQ", "N", "Technology", "HW",
               DAYS[0], DAYS[-1], t, t, "stocks"))


def _price(c, t, d, close, volume=1_000_000):
    c.execute("INSERT OR REPLACE INTO prices (ticker, date, close, volume, adjustment)"
              " VALUES (?,?,?,?,1.0)", (t, d, close, volume))


def _filing(c, t, d, shares, dim="ARQ", wa=None, factor=None, bas=True):
    # sharesbas is the period-end count; shareswa (the quarter's weighted average) defaults to the same
    c.execute("INSERT OR REPLACE INTO sf1 (ticker, dimension, date, reportperiod, sharesbas, sharefactor, shareswa)"
              " VALUES (?,?,?,?,?,?,?)", (t, dim, d, d, shares if bas else None, factor,
                                          wa if wa is not None else shares))


def _split(c, t, d, ratio):
    c.execute("INSERT OR REPLACE INTO corporate_actions (ticker, date, action, value)"
              " VALUES (?,?,?,?)", (t, d, "split", str(ratio)))


@pytest.fixture
def c(tmp_path):
    conn = db.connect(tmp_path / "pit.db")
    # FWD: 1M shares then a 7:1 split on 06-09 and a 4:1 split far in the future; the vendor
    # serves the restated count (28M), and the as-traded price falls 700 -> 100 on 06-09.
    _stock(conn, "FWD")
    _filing(conn, "FWD", "2014-05-01", 28_000_000)
    _split(conn, "FWD", "2014-06-09", 7.0)
    _split(conn, "FWD", "2020-08-31", 4.0)
    for d in DAYS:
        _price(conn, "FWD", d, 700.0 if d < "2014-06-09" else 100.0)
    # REV: 1:10 reverse split on 06-09: 50M shares at $10 become 5M at $100; restated count 5M.
    _stock(conn, "REV")
    _filing(conn, "REV", "2014-05-01", 5_000_000)
    _split(conn, "REV", "2014-06-09", 0.1)
    for d in DAYS:
        _price(conn, "REV", d, 10.0 if d < "2014-06-09" else 100.0)
    conn.commit()
    yield conn
    conn.close()


def test_pit_share_events_undo_later_splits(c):
    ev = pit_share_events(c, "FWD")
    assert [e[0] for e in ev] == ["2014-05-01", "2014-06-09", "2020-08-31"]
    assert [e[1] for e in ev] == pytest.approx([1_000_000, 7_000_000, 28_000_000])
    assert [e[4] for e in ev] == pytest.approx([28.0, 4.0, 1.0])
    assert all(e[2] == "2014-05-01" and e[3] == 28_000_000 and e[5] == "sharesbas" for e in ev)
    rev = pit_share_events(c, "REV")
    assert [e[1] for e in rev] == pytest.approx([50_000_000, 5_000_000])


def test_arq_wins_on_a_shared_filing_date(c):
    _filing(c, "FWD", "2014-05-01", 99_000_000, dim="ARY")
    c.commit()
    assert pit_share_events(c, "FWD")[0][3] == 28_000_000


def test_mcap_continuous_across_splits(c):
    build_universe_pit_history(c, min_price=1.0, min_mcap=1.0, min_dvol=1.0, lookback=3,
                               min_dvol_days=1, max_quote_age=10, types=("Domestic Common Stock",))
    for t, cap in [("FWD", 700e6), ("REV", 500e6)]:
        caps = [r[0] for r in c.execute("SELECT mcap FROM universe_pit WHERE ticker=? ORDER BY as_of", (t,))]
        assert len(caps) == len(DAYS)
        assert caps == pytest.approx([cap] * len(DAYS))


def test_mcap_screen_uses_pit_shares(c):
    # The restated count would put FWD at 700 x 28M = $19.6B before the split; its real cap is $700M.
    build_universe_pit_history(c, min_price=1.0, min_mcap=1e9, min_dvol=1.0, lookback=3,
                               min_dvol_days=1, max_quote_age=10, types=("Domestic Common Stock",))
    assert c.execute("SELECT COUNT(*) FROM universe_pit WHERE ticker='FWD'").fetchone()[0] == 0


def test_history_rebuild_removes_members_that_no_longer_qualify(c):
    kw = dict(min_price=1.0, min_dvol=1.0, lookback=3, min_dvol_days=1, max_quote_age=10,
              types=("Domestic Common Stock",))
    build_universe_pit_history(c, min_mcap=1.0, **kw)
    assert c.execute("SELECT COUNT(*) FROM universe_pit").fetchone()[0] == 2 * len(DAYS)
    build_universe_pit_history(c, min_mcap=600e6, **kw)          # REV ($500M) drops out
    assert {r[0] for r in c.execute("SELECT DISTINCT ticker FROM universe_pit")} == {"FWD"}
    # a windowed rebuild leaves the rows outside its window alone
    build_universe_pit_history(c, min_mcap=1e12, start="2014-06-12", **kw)
    assert c.execute("SELECT COUNT(*) FROM universe_pit WHERE as_of < '2014-06-12'").fetchone()[0] == 8
    assert c.execute("SELECT COUNT(*) FROM universe_pit WHERE as_of >= '2014-06-12'").fetchone()[0] == 0


def test_single_date_build_uses_pit_shares_and_replaces_the_date(c):
    n = build_universe_pit(c, as_of="2014-06-06", min_price=1.0, min_mcap=1.0, min_dvol=1.0,
                           lookback=3, min_dvol_days=1, max_quote_age=10)
    assert n == 2
    cap = c.execute("SELECT mcap FROM universe_pit WHERE ticker='FWD' AND as_of='2014-06-06'").fetchone()[0]
    assert cap == pytest.approx(700e6)
    n = build_universe_pit(c, as_of="2014-06-06", min_price=1.0, min_mcap=600e6, min_dvol=1.0,
                           lookback=3, min_dvol_days=1, max_quote_age=10)
    assert n == 1
    assert {r[0] for r in c.execute("SELECT ticker FROM universe_pit WHERE as_of='2014-06-06'")} == {"FWD"}


def test_build_shares_pit_table_and_dictionary(c):
    n = build_shares_pit(c)
    assert n == 5
    rows = c.execute("SELECT from_date, shares, split_factor FROM shares_pit WHERE ticker='FWD'"
                     " ORDER BY from_date").fetchall()
    assert [r[0] for r in rows] == ["2014-05-01", "2014-06-09", "2020-08-31"]
    assert [r[1] for r in rows] == pytest.approx([1e6, 7e6, 28e6])
    assert build_shares_pit(c) == 5                                # idempotent
    cols = {r[0] for r in c.execute("SELECT indicator FROM descriptions WHERE table_name='shares_pit'")}
    assert cols == {"ticker", "from_date", "shares", "filing_date", "restated_shares", "split_factor", "share_field"}


def test_share_count_is_period_end_basic_times_factor_with_weighted_average_fallback(c):
    # period-end count differs from the quarter's weighted average (issuance during the quarter)
    _filing(c, "ISS", "2014-05-01", 10_000_000, wa=6_000_000)
    _filing(c, "ADR", "2014-05-01", 10_000_000, factor=0.5)        # 2 shares per ADR
    _filing(c, "OLD", "2014-05-01", 3_000_000, bas=False)          # no sharesbas: weighted average
    c.commit()
    assert pit_share_events(c, "ISS")[0][1] == pytest.approx(10_000_000)
    assert pit_share_events(c, "ADR")[0][1] == pytest.approx(5_000_000)
    old = pit_share_events(c, "OLD")[0]
    assert old[1] == pytest.approx(3_000_000) and old[5] == "shareswa"


def test_connect_adds_share_field_to_an_existing_shares_pit(tmp_path):
    import sqlite3
    p = tmp_path / "legacy.db"
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE shares_pit (ticker TEXT, from_date TEXT, shares REAL, filing_date TEXT,"
                " restated_shares REAL, split_factor REAL, PRIMARY KEY (ticker, from_date))")
    raw.commit(); raw.close()
    conn = db.connect(p)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shares_pit)")}
    assert "share_field" in cols


def test_failing_quote_ends_membership_that_day(tmp_path):
    """A quote that fails the screens ends membership; only a gap in quotes is bridged (LEHMQ 2008-09)."""
    conn = db.connect(tmp_path / "fail.db")
    days = ["2008-09-%02d" % d for d in (2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 22, 23)]
    for t in ("BUST", "GAPPY"):
        conn.execute('INSERT INTO securities_master (ticker, category, exchange, isdelisted, sector, industry,'
                     ' firstpricedate, lastpricedate, name, permaticker, "table") VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (t, "Domestic Common Stock", "NYSE", "N", "Financial Services", "Banks", days[0], days[-1], t, t, "stocks"))
        _filing(conn, t, "2008-08-01", 10_000_000)
    for d in days:      # BUST trades at $40 until 09-12, then $0.30 (fails the $2 floor) every day after
        _price(conn, "BUST", d, 40.0 if d <= "2008-09-12" else 0.30)
    for d in days:      # GAPPY trades at $40 but has no quotes 09-15..09-18 (a gap, not a failure)
        if not ("2008-09-15" <= d <= "2008-09-18"):
            _price(conn, "GAPPY", d, 40.0)
    conn.commit()
    build_universe_pit_history(conn, min_price=2.0, min_mcap=1.0, min_dvol=1.0, lookback=3,
                               min_dvol_days=1, max_quote_age=10, types=("Domestic Common Stock",))
    bust = [r[0] for r in conn.execute("SELECT as_of FROM universe_pit WHERE ticker='BUST' ORDER BY as_of")]
    assert bust[-1] == "2008-09-12" and "2008-09-15" not in bust
    gappy = {r[0] for r in conn.execute("SELECT as_of FROM universe_pit WHERE ticker='GAPPY'")}
    assert {"2008-09-15", "2008-09-16", "2008-09-17", "2008-09-18"} <= gappy      # bridged on the last quote
    conn.close()


def test_derived_fundamentals_carry_the_filing_date(tmp_path):
    """fundamentals and quarterly_statements record when each row became known (its SF1 filing date)."""
    from data_manager.universe import build_piotroski, build_quarterly
    conn = db.connect(tmp_path / "fund.db")
    _stock(conn, "FUND")
    for dim, cal, rp, filed in [("ARY", "2012-12-31", "2012-12-31", "2013-02-27"), ("ARY", "2013-12-31", "2013-12-31", "2014-02-26"),
                                ("ARQ", "2013-12-31", "2013-12-31", "2014-02-26")]:
        conn.execute("INSERT INTO sf1 (ticker, dimension, calendardate, date, reportperiod, netinc, assets, ncfo,"
                     " liabilities, assetsc, liabilitiesc, shareswa, gp, revenue) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("FUND", dim, cal, filed, rp, 10.0, 100.0, 12.0, 40.0, 30.0, 20.0, 5.0, 8.0, 50.0))
    conn.commit()
    build_piotroski(conn); build_quarterly(conn)
    assert dict(conn.execute("SELECT fiscal_year, filing_date FROM fundamentals WHERE ticker='FUND'").fetchall()) == {
        2012: "2013-02-27", 2013: "2014-02-26"}
    assert conn.execute("SELECT filing_date FROM quarterly_statements WHERE ticker='FUND'").fetchone()[0] == "2014-02-26"
    conn.close()
