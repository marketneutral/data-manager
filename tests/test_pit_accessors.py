"""pit accessors: as-of on any calendar date, split basis, restated dimensions refused."""
import pytest

from data_manager import db, pit
from data_manager.universe import build_shares_pit, build_universe_pit_history
from test_pit_shares import DAYS, _filing, _price, _split, _stock


@pytest.fixture
def c(tmp_path):
    conn = db.connect(tmp_path / "acc.db")
    _stock(conn, "FWD")
    _filing(conn, "FWD", "2014-05-01", 28_000_000)
    conn.execute("UPDATE sf1 SET eps = 0.5, revenue = 1e9 WHERE ticker='FWD'")          # restated: 0.5 now = 14.0 then
    conn.execute("INSERT INTO sf1 (ticker, dimension, date, reportperiod, eps) VALUES ('FWD','MRQ','2014-05-01','2014-03-31', 0.5)")
    _split(conn, "FWD", "2014-06-09", 7.0)
    _split(conn, "FWD", "2020-08-31", 4.0)
    for d in DAYS:
        _price(conn, "FWD", d, 700.0 if d < "2014-06-09" else 100.0)
    conn.commit()
    build_shares_pit(conn)
    build_universe_pit_history(conn, min_price=2.0, min_mcap=1.0, min_dvol=1.0, lookback=3, min_dvol_days=1,
                               max_quote_age=10, types=("Domestic Common Stock",))
    yield conn
    conn.close()


def test_fundamentals_on_the_as_of_split_basis(c):
    before = pit.pit_fundamentals("2014-06-06", ["eps", "sharesbas", "revenue"], conn=c)["FWD"]
    assert before["eps"] == pytest.approx(14.0) and before["sharesbas"] == pytest.approx(1e6)
    assert before["revenue"] == 1e9 and before["filing_date"] == "2014-05-01"
    after = pit.pit_fundamentals("2014-06-10", ["eps"], conn=c)["FWD"]
    assert after["eps"] == pytest.approx(2.0)                                 # 0.5 x 4: only the 2020 split is later
    raw = pit.pit_fundamentals("2014-06-06", ["eps"], conn=c, split_basis=False)["FWD"]
    assert raw["eps"] == 0.5


def test_nothing_known_before_the_filing(c):
    assert pit.pit_fundamentals("2014-04-30", ["eps"], conn=c) == {}


def test_restated_dimension_refused_unless_allowed(c):
    with pytest.raises(ValueError, match="look-ahead"):
        pit.pit_fundamentals("2014-06-06", ["eps"], dimension="MRQ", conn=c)
    assert "FWD" in pit.pit_fundamentals("2014-06-06", ["eps"], dimension="MRQ", conn=c, allow_restated=True)


def test_market_cap_continuous_across_the_split(c):
    assert pit.pit_market_cap("2014-06-06", ["FWD"], conn=c)["FWD"] == pytest.approx(700e6)
    assert pit.pit_market_cap("2014-06-09", ["FWD"], conn=c)["FWD"] == pytest.approx(700e6)


def test_members_on_a_weekend_use_the_last_trading_day(c):
    m = pit.pit_members("2014-06-07", conn=c)                                   # a Saturday
    assert m["FWD"]["as_of"] == "2014-06-06" and m["FWD"]["mcap"] == pytest.approx(700e6)
