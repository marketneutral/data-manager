"""The store: a synthetic vendor that splits, pays dividends, rewrites history, drops rows and recycles tickers."""
import json

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pyarrow")

from data_manager.store import load as L, read as R, sharadar as A
from data_manager.store.schema import connect

DAYS = pd.bdate_range("2020-01-01", periods=60).strftime("%Y-%m-%d").tolist()


def company(ticker, perma, start=40.0, splits=(), dividends=(), seed=0, shares=1e8, n=60):
    rng = np.random.default_rng(seed)
    raw = start * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    vol = rng.integers(10_000, 50_000, n).astype(float) * 100
    for d, s in splits:                       # as traded, the price falls and volume rises at the split
        raw[d:] /= s
        vol[d:] *= s
    return dict(ticker=ticker, perma=perma, raw=raw, vol=vol, splits=list(splits), dividends=list(dividends),
                shares=shares, days=list(range(n)))


def vendor(tmp, comps, upto, name="v", drop=None):
    """Vendor files as of day `upto` (inclusive): split-adjusted with every split known by then."""
    rows, acts, tick, fund = [], [], [], []
    for c in comps:
        days = [d for d in c["days"] if d <= upto and not (drop and (c["ticker"], d) in drop)]
        known = [(d, s) for d, s in c["splits"] if d <= upto]
        F = lambda t: np.prod([s for d, s in known if d > t]) if known else 1.0
        E = np.ones(len(c["raw"]))
        for d, s in known:
            E[d] /= s
        for d, a in c["dividends"]:
            if d <= upto:
                E[d] *= 1 - a / c["raw"][d - 1]
        for t in days:
            adj = np.prod(E[t + 1:upto + 1])
            f = F(t)
            r = c["raw"][t]
            rows.append(dict(ticker=c["ticker"], date=DAYS[t], open=round(r * 0.99 / f, 3), high=round(r * 1.01 / f, 3),
                             low=round(r * 0.98 / f, 3), close=round(r / f, 3), volume=c["vol"][t] * f,
                             closeadj=round(r * adj, 3), closeunadj=round(r, 3), lastupdated=DAYS[upto]))
        for d, s in known:
            acts.append(dict(date=DAYS[d], action="split", ticker=c["ticker"], name="x", value=s))
        for d, a in c["dividends"]:
            if d <= upto:
                acts.append(dict(date=DAYS[d], action="dividend", ticker=c["ticker"], name="x", value=round(a / F(d), 5)))
        for table in ("SEP", "SF1"):
            tick.append(dict(table=table, permaticker=c["perma"], ticker=c["ticker"], name=c["ticker"] + " INC",
                             exchange="NYSE", isdelisted="N", category="Domestic Common Stock", cusips="", figi="",
                             famaindustry="", sector="Technology", industry="Software", sicsector="", sicindustry="",
                             firstpricedate=DAYS[days[0]], lastpricedate=DAYS[days[-1]]))
        for fd in (10, 45):
            if fd <= upto and fd in days:
                fund.append(dict(ticker=c["ticker"], dimension="ARQ", date=DAYS[fd], reportperiod=DAYS[fd - 5],
                                 sharesbas=c["shares"] * F(fd), sharefactor=1.0, shareswa=c["shares"] * F(fd),
                                 marketcap=c["shares"] * c["raw"][fd]))
    out = {}
    for k, frame in (("stocks", rows), ("actions", acts), ("tickers", tick), ("fundamentals", fund)):
        p = tmp / f"{name}_{k}.csv"
        cols = {"stocks": A.PRICE_COLS + ["lastupdated"], "actions": ["date", "action", "ticker", "name", "value"],
                "tickers": A.TICKER_COLS, "fundamentals": A.FUND_COLS}[k]
        pd.DataFrame(frame, columns=cols).to_csv(p, index=False)
        out[k] = str(p)
    return out


@pytest.fixture
def comps():
    return [company("AAA", "101", splits=[(30, 2.0)], dividends=[(20, 0.5)], seed=1),
            company("BBB", "102", start=12.0, seed=2, splits=[(25, 0.1)]),
            company("CCC", "103", start=80.0, seed=3, dividends=[(15, 1.0), (40, 1.0)])]


def store(tmp):
    return str(tmp / "warehouse.db")


def test_the_adapter_recovers_what_traded(tmp_path, comps):
    facts = A.normalize(A.read_files(vendor(tmp_path, comps, 59)))
    px = facts["prices"].set_index(["security_id", "date"])
    a = comps[0]
    for t in (0, 29, 30, 59):                                    # before and after AAA's 2:1 split
        row = px.loc[("101", DAYS[t])]
        assert row.close_traded == pytest.approx(round(a["raw"][t], 3))
        assert row.volume_traded == pytest.approx(a["vol"][t])      # whole shares, as printed
    sp = facts["splits"].set_index("security_id")
    assert sp.loc["101", "ex_date"] == DAYS[30] and sp.loc["101", "ratio"] == 2.0 and sp.loc["101", "how"] == "action"
    assert sp.loc["102", "ratio"] == pytest.approx(0.1)          # a 1:10 reverse split
    dv = facts["dividends"].set_index(["security_id", "ex_date"])
    assert dv.loc[("101", DAYS[20]), "amount_traded"] == pytest.approx(0.5, abs=1e-4)   # in that day's units
    sh = facts["shares"].set_index(["security_id", "filing_date"])
    assert sh.loc[("101", DAYS[10]), "shares_filed"] == pytest.approx(1e8)                 # as filed, not restated
    assert facts["other_adjustments"].empty


def test_a_first_load_is_accepted_and_audited(tmp_path, comps):
    r = L.load(vendor(tmp_path, comps, 59), store=store(tmp_path), hash_files=False)
    assert r["status"] == "accepted"
    a = r["summary"]["audit"]
    assert a["volume_whole_shares"]["share"] == 1.0
    assert a["total_return"]["within_rounding"] > 0.99
    assert a["market_cap"]["within_1pct"] == 1.0
    conn = connect(store(tmp_path), readonly=True)
    assert len(R.rows(conn, "prices")) == 180 and len(R.rows(conn, "splits")) == 2


def test_a_new_split_rewrites_the_vendor_s_history_but_not_ours(tmp_path, comps):
    comps[2]["splits"] = [(55, 3.0)]                               # CCC splits 3:1 on day 55
    comps[2]["raw"][55:] /= 3.0
    comps[2]["vol"][55:] *= 3.0
    s = store(tmp_path)
    L.load(vendor(tmp_path, comps, 50, "a"), store=s, hash_files=False)
    r = L.load(vendor(tmp_path, comps, 59, "b"), store=s, hash_files=False)
    c = r["summary"]["counts"]
    assert r["status"] == "accepted"
    assert c["prices"] == {"added": 27, "changed": 0, "missing": 0}         # 9 new days x 3, nothing rewritten
    assert c["splits"]["added"] == 1 and c["shares"]["changed"] == 0 and c["dividends"]["changed"] == 0


def test_a_nightly_increment_adds_the_new_day(tmp_path, comps):
    s = store(tmp_path)
    L.load(vendor(tmp_path, comps, 58, "a"), store=s, hash_files=False)
    full = vendor(tmp_path, comps, 59, "b")
    day = pd.read_csv(full["stocks"]); day[day.date == DAYS[59]].to_csv(tmp_path / "inc.csv", index=False)
    r = L.load(dict(full, stocks=str(tmp_path / "inc.csv")), mode="increment", store=s, hash_files=False)
    assert r["status"] == "accepted" and r["summary"]["counts"]["prices"]["added"] == 3
    assert r["summary"]["counts"]["prices"]["missing"] == 0                  # an increment infers nothing about the rest
    conn = connect(s, readonly=True)
    assert len(R.rows(conn, "prices")) == 180 and len(R.rows(conn, "splits")) == 2   # the known splits are kept


def test_dropped_history_is_held_back_and_kept(tmp_path, comps):
    s = store(tmp_path)
    first = L.load(vendor(tmp_path, comps, 59, "a"), store=s, hash_files=False)["load_id"]
    drop = {("BBB", d) for d in range(0, 20)}
    r = L.load(vendor(tmp_path, comps, 59, "b", drop=drop), store=s, hash_files=False, config=dict(max_dropped_rows=10))
    assert r["status"] == "quarantined"
    assert any(f["kind"] == "price history dropped" and f["severity"] == "hold" and f["count"] == 20 for f in r["summary"]["findings"])
    conn = connect(s, readonly=True)
    assert len(R.rows(conn, "prices")) == 180                                # nothing changed while held back
    ok = L.accept(r["load_id"], reviewed_by="cio", store=s)
    assert ok["status"] == "accepted"
    conn = connect(s, readonly=True)
    assert len(R.rows(conn, "prices")) == 160                                # dropped from the current view
    assert len(R.rows(conn, "prices", at=first)) == 180                      # and still there as of the first load


def test_many_old_prices_changing_is_held_back(tmp_path, comps):
    s = store(tmp_path)
    L.load(vendor(tmp_path, comps, 59, "a"), store=s, hash_files=False)
    comps[1]["raw"][:20] *= 1.1                                             # the vendor revises BBB's early history
    r = L.load(vendor(tmp_path, comps, 59, "b"), store=s, hash_files=False, config=dict(max_changed_old_rows=5))
    assert r["status"] == "quarantined"
    assert any(f["kind"] == "old prices changed" and f["count"] == 20 for f in r["summary"]["findings"])


def test_a_recycled_ticker_cannot_mix_two_companies(tmp_path, comps):
    s = store(tmp_path)
    L.load(vendor(tmp_path, comps, 59, "a"), store=s, hash_files=False)
    old = comps[1]; old["ticker"] = "BBB1"                                   # the vendor renames the old company
    new = company("BBB", "999", start=5.0, seed=9, n=60); new["days"] = list(range(50, 60))
    r = L.load(vendor(tmp_path, comps + [new], 59, "b"), store=s, hash_files=False)
    assert r["status"] == "accepted"
    conn = connect(s, readonly=True)
    px = R.rows(conn, "prices")
    assert (px.security_id == "102").sum() == 60 and (px.security_id == "999").sum() == 10
    sec = R.rows(conn, "securities").set_index("security_id")
    assert sec.loc["102", "ticker"] == "BBB1" and sec.loc["999", "ticker"] == "BBB"


def test_adjusted_prices_are_total_returns_as_of_a_date(tmp_path, comps):
    s = store(tmp_path)
    L.load(vendor(tmp_path, comps, 59), store=s, hash_files=False)
    conn = connect(s, readonly=True)
    px, sp, dv, ot = (R.rows(conn, t) for t in ("prices", "splits", "dividends", "other_adjustments"))
    adj = R.adjusted(px, sp, dv, ot).set_index(["security_id", "date"])
    a = comps[0]
    # across the split and the dividend, the adjusted return is the total return
    r_split = adj.loc[("101", DAYS[30]), "close"] / adj.loc[("101", DAYS[29]), "close"]
    assert r_split == pytest.approx(round(a["raw"][30], 3) * 2 / round(a["raw"][29], 3), rel=1e-6)
    r_div = adj.loc[("101", DAYS[20]), "close"] / adj.loc[("101", DAYS[19]), "close"]
    assert r_div == pytest.approx(round(a["raw"][20], 3) / (round(a["raw"][19], 3) - 0.5), rel=1e-6)   # the vendor's convention
    # as of day 25, AAA's day-30 split is unknown: day 0 is scaled only by the dividend
    early = R.adjusted(px, sp, dv, ot, as_of=DAYS[25]).set_index(["security_id", "date"])
    assert early.loc[("101", DAYS[25]), "close"] == pytest.approx(round(a["raw"][25], 3))
    assert early.loc[("101", DAYS[0]), "close"] == pytest.approx(round(a["raw"][0], 3) * (1 - 0.5 / round(a["raw"][19], 3)), rel=1e-6)


def test_shares_on_each_day_follow_filings_and_splits(tmp_path, comps):
    s = store(tmp_path)
    L.load(vendor(tmp_path, comps, 59), store=s, hash_files=False)
    conn = connect(s, readonly=True)
    on = R.shares_on(R.rows(conn, "shares"), R.rows(conn, "splits"), "101", DAYS)
    assert np.isnan(on[DAYS[5]])                                              # before the first filing
    assert on[DAYS[10]] == pytest.approx(1e8) and on[DAYS[29]] == pytest.approx(1e8)
    assert on[DAYS[30]] == pytest.approx(2e8)                                 # the 2:1 split doubles the count
    assert on[DAYS[45]] == pytest.approx(1e8 * 1)                             # the day-45 filing is in its own units


def test_the_loads_table_records_files_and_outcome(tmp_path, comps):
    s = store(tmp_path)
    L.load(vendor(tmp_path, comps, 59), store=s, hash_files=True)
    conn = connect(s, readonly=True)
    ld = R.loads(conn).iloc[0]
    assert ld.status == "accepted" and ld.data_through == DAYS[59]
    assert len(json.loads(ld.files)["stocks"]["sha256"]) == 64


def test_an_increment_with_a_new_split_re_sends_history_and_changes_nothing_old(tmp_path, comps):
    """The night a split happens, the vendor re-sends the stock's whole history rescaled; only the new day and the
    split are new to us."""
    comps[2]["splits"] = [(59, 4.0)]
    comps[2]["raw"][59:] /= 4.0
    comps[2]["vol"][59:] *= 4.0
    s = store(tmp_path)
    L.load(vendor(tmp_path, comps, 58, "a"), store=s, hash_files=False)
    full = vendor(tmp_path, comps, 59, "b")
    st = pd.read_csv(full["stocks"])
    inc = st[(st.ticker == "CCC") | (st.date == DAYS[59])]                  # CCC's whole history, the others' new day
    inc.to_csv(tmp_path / "inc.csv", index=False)
    r = L.load(dict(full, stocks=str(tmp_path / "inc.csv")), mode="increment", store=s, hash_files=False)
    c = r["summary"]["counts"]
    assert r["status"] == "accepted"
    assert c["prices"] == {"added": 3, "changed": 0, "missing": 0}
    conn = connect(s, readonly=True)
    sp = R.rows(conn, "splits").set_index("security_id")
    assert len(sp) == 3 and sp.loc["103", "ratio"] == 4.0 and sp.loc["103", "ex_date"] == DAYS[59]
    on = R.rows(conn, "prices").set_index(["security_id", "date"])
    assert on.loc[("103", DAYS[58]), "volume_traded"] == pytest.approx(comps[2]["vol"][58])   # still as traded
