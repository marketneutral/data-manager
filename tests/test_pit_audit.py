"""pit-audit catches the point-in-time breaks it is meant to catch, and records every run."""
import datetime as dt

from data_manager import db, pit_audit
from data_manager.universe import build_shares_pit, build_universe_pit_history
from test_pit_shares import DAYS, _filing, _price, _split, _stock


def _healthy(tmp_path):
    c = db.connect(tmp_path / "audit.db")
    _stock(c, "FWD")
    _filing(c, "FWD", "2014-05-01", 28_000_000)
    _split(c, "FWD", "2014-06-09", 7.0)
    _split(c, "FWD", "2020-08-31", 4.0)
    for d in DAYS:
        _price(c, "FWD", d, 700.0 if d < "2014-06-09" else 100.0)
    c.commit()
    build_shares_pit(c)
    build_universe_pit_history(c, min_price=2.0, min_mcap=1.0, min_dvol=1.0, lookback=3, min_dvol_days=1,
                               max_quote_age=10, types=("Domestic Common Stock",))
    return c


def _status(c, name):
    return {r["check"]: r["status"] for r in pit_audit.run(c, record=False, verbose=False)["results"]}[name]


def test_healthy_warehouse_passes_the_pit_checks(tmp_path):
    c = _healthy(tmp_path)
    for name in ["member_price_floor", "member_profile_is_same_day", "mcap_continuous_across_splits",
                 "shares_pit_split_consistent", "universe_covers_prices", "split_events_distinct",
                 "split_events_current"]:
        assert _status(c, name) == "ok", name


def test_stale_member_profile_is_an_error(tmp_path):
    c = _healthy(tmp_path)
    c.execute("UPDATE universe_pit SET price = price * 2 WHERE as_of = ?", (DAYS[-1],))
    c.commit()
    assert _status(c, "member_profile_is_same_day") == "error"


def test_member_below_price_floor_is_an_error(tmp_path):
    c = _healthy(tmp_path)
    c.execute("UPDATE prices SET close = 1.0 WHERE date = ?", (DAYS[-1],))
    c.commit()
    assert _status(c, "member_price_floor") == "error"


def test_restated_share_mcap_breaks_split_continuity(tmp_path):
    c = _healthy(tmp_path)
    c.execute("UPDATE universe_pit SET mcap = price * 28000000")           # the pre-fix close x shareswa
    c.commit()
    assert _status(c, "mcap_continuous_across_splits") == "error"


def test_split_counted_twice_is_an_error(tmp_path):
    c = _healthy(tmp_path)
    c.execute("INSERT INTO split_events (ticker, date, ratio, raw_rows, rule) VALUES ('FWD', '2014-06-06', 7.0, 1, 'single')")
    c.commit()
    assert _status(c, "split_events_distinct") == "error"


def test_split_events_not_rebuilt_after_new_splits_is_an_error(tmp_path):
    c = _healthy(tmp_path)
    _split(c, "FWD", "2024-06-10", 10.0)
    c.commit()
    assert _status(c, "split_events_current") == "error"

def test_freshness_is_a_warning_not_an_error(tmp_path):
    c = _healthy(tmp_path)
    status, age, _ = pit_audit.check_freshness(c, today=dt.date(2014, 7, 30))
    assert status == "warn" and age > 10


def test_runs_are_recorded_and_cli_exits_nonzero_on_error(tmp_path):
    from data_manager import cli
    c = _healthy(tmp_path)
    res = pit_audit.run(c, verbose=False)
    assert c.execute("SELECT COUNT(*) FROM pit_audit_runs WHERE run_at = ?", (res["run_at"],)).fetchone()[0] == len(pit_audit.CHECKS)
    c.execute("UPDATE prices SET close = 1.0 WHERE date = ?", (DAYS[-1],))
    c.commit(); c.close()
    assert cli.main(["pit-audit", "--db", str(tmp_path / "audit.db"), "--no-record"]) == 1
