"""The panel built from the store: each field behaves as its declaration says at splits and dividends."""
import json

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pyarrow")

from data_manager import panel as P
from data_manager.store import load as L

from test_store import DAYS, company, vendor


@pytest.fixture
def built(tmp_path):
    comps = [company("AAA", "101", splits=[(30, 2.0)], dividends=[(20, 0.5)], seed=1),
             company("BBB", "102", start=12.0, seed=2, splits=[(25, 0.1)]),
             company("SPY", "900", start=300.0, seed=4, dividends=[(35, 1.5)])]
    files = vendor(tmp_path, comps, 59)
    t = pd.read_csv(files["tickers"])                                    # SPY is a fund
    t.loc[t.ticker == "SPY", "table"] = np.where(t.loc[t.ticker == "SPY", "table"] == "SEP", "SFP", "SF1X")
    t.to_csv(files["tickers"], index=False)
    st = pd.read_csv(files["stocks"]); st[st.ticker == "SPY"].to_csv(tmp_path / "funds.csv", index=False)
    st[st.ticker != "SPY"].to_csv(files["stocks"], index=False)
    files["funds"] = str(tmp_path / "funds.csv")
    s = str(tmp_path / "w.db")
    assert L.load(files, store=s, hash_files=False)["status"] == "accepted"
    folder, m = P.build(tmp_path / "panels", store=s)
    return folder, m, comps


def read(folder, f):
    return pd.read_parquet(folder / f"{f}.parquet")


def test_fields_at_a_split(built):
    folder, m, comps = built
    d0, d1 = pd.Timestamp(DAYS[29]), pd.Timestamp(DAYS[30])              # AAA's 2:1 split on day 30
    price, close, shares = read(folder, "price")["AAA"], read(folder, "close")["AAA"], read(folder, "shares")["AAA"]
    vt, vol = read(folder, "volume_traded")["AAA"], read(folder, "volume")["AAA"]
    assert price[d1] / price[d0] == pytest.approx(comps[0]["raw"][30] / comps[0]["raw"][29], rel=1e-3)   # falls by ~2
    assert close[d1] / close[d0] == pytest.approx(2 * comps[0]["raw"][30] / comps[0]["raw"][29], rel=1e-3)  # smooth
    assert shares[d1] / shares[d0] == pytest.approx(2.0)
    assert (price * shares)[d1] / (price * shares)[d0] == pytest.approx(2 * comps[0]["raw"][30] / comps[0]["raw"][29], rel=1e-3)
    assert vt[d0] == pytest.approx(comps[0]["vol"][29]) and vt[d1] == pytest.approx(comps[0]["vol"][30])  # as printed
    assert (close * vol)[d0] == pytest.approx((price * vt)[d0], rel=1e-9)                                  # dollars traded


def test_the_manifest_declares_every_field(built):
    folder, m, _ = built
    disk = json.loads((folder / "panel.json").read_text())
    assert disk["version"] == m["version"] == folder.name
    assert disk["fields"]["close"]["adjustment"] == 1 and disk["fields"]["price"]["adjustment"] == 0
    assert disk["fields"]["volume"]["adjustment"] == -1 and disk["fields"]["sector"]["unit"] == "label"
    assert set(disk["files"]) >= {"close.parquet", "price.parquet", "shares.parquet", "market.parquet", "labels.parquet"}
    assert disk["tickers"] == {"AAA": "101", "BBB": "102"}                # SPY is the market, not a stock


def test_market_and_labels(built):
    folder, m, comps = built
    mk = read(folder, "market")["SPY"]
    d0, d1 = pd.Timestamp(DAYS[34]), pd.Timestamp(DAYS[35])               # SPY's dividend on day 35
    assert mk[d1] / mk[d0] == pytest.approx(round(comps[2]["raw"][35], 3) / (round(comps[2]["raw"][34], 3) - 1.5), rel=1e-6)
    assert read(folder, "labels").loc["AAA", "sector"] == "Technology"


def test_the_same_store_builds_the_same_version(built, tmp_path):
    folder, m, _ = built
    again, m2 = P.build(tmp_path / "panels", store=str(tmp_path / "w.db"))
    assert m2["version"] == m["version"] and again == folder
