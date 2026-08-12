"""Tests for Ken French factor ingestion (factors.py / bulkload.py)."""
import io
import os
import zipfile

import pytest

from data_manager import bulk, bulkload, db, factors


def _zip_bytes(csv_text, name="f.csv"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, csv_text)
    return buf.getvalue()


SAMPLE_3F = (
    "This file was created by using the 202606 CRSP database.\n"
    ",Mkt-RF,SMB,HML,RF\n"
    "19260701,    0.09,   -0.25,   -0.27,    0.01\n"
    "19260702,    0.45,   -0.33,   -0.06,    0.01\n"
    "19260706,-99.99,    0.30,   -0.39,  -99.99\n"   # vendor sentinel -> NULL
)


def test_load_french_factor_file_stores_dates_and_nulls(conn, tmp_path):
    p = tmp_path / "3f.zip"
    p.write_bytes(_zip_bytes(SAMPLE_3F))
    spec = factors.FACTOR_FILES["3f"]
    n = bulkload.load_french_factor_file(str(p), conn, cols=spec["cols"],
                                         vcols=spec["vcols"])
    assert n == 3
    rows = conn.execute(
        "SELECT date, mkt_rf, smb, hml, rf FROM french_factors ORDER BY date").fetchall()
    assert [r["date"] for r in rows] == ["1926-07-01", "1926-07-02", "1926-07-06"]
    assert rows[0]["mkt_rf"] == pytest.approx(0.09)      # YYYYMMDD -> YYYY-MM-DD
    assert rows[2]["mkt_rf"] is None                     # -99.99 -> NULL
    assert rows[2]["smb"] == pytest.approx(0.30)
    assert rows[2]["rf"] is None


def test_load_french_factor_file_is_idempotent(conn, tmp_path):
    p = tmp_path / "mom.zip"
    p.write_bytes(_zip_bytes("note line\n,Mom\n19261103, 0.35\n19261104,-99.99\n"))
    spec = factors.FACTOR_FILES["mom"]
    bulkload.load_french_factor_file(str(p), conn, cols=spec["cols"], vcols=spec["vcols"])
    bulkload.load_french_factor_file(str(p), conn, cols=spec["cols"], vcols=spec["vcols"])
    assert conn.execute("SELECT COUNT(*) FROM french_factors").fetchone()[0] == 2


def test_header_drift_warns_but_loads(conn, tmp_path, capsys):
    p = tmp_path / "drift.zip"
    p.write_bytes(_zip_bytes(",MKT-RF,SMB,HML,RF\n19260701, 1, 2, 3, 0.01\n"))
    spec = factors.FACTOR_FILES["3f"]
    n = bulkload.load_french_factor_file(str(p), conn, cols=spec["cols"], vcols=spec["vcols"])
    assert n == 1
    assert "header drift" in capsys.readouterr().out


def test_update_french_factors_downloads_loads_and_ledgers(conn, tmp_path, monkeypatch):
    dest = tmp_path / "bulk"
    one = {
        "3f": ",Mkt-RF,SMB,HML,RF\n19260701, 0.09, -0.25, -0.27, 0.01\n",
        "5f": ",Mkt-RF,SMB,HML,RMW,CMA,RF\n19630701, -0.67, 0.00, -0.34, -0.01, 0.16, 0.01\n",
        "mom": ",Mom\n19261103, 0.35\n",
        "st_rev": ",ST_Rev\n19260126, 0.13\n",
        "lt_rev": ",LT_Rev\n19300320, -0.30\n",
    }

    def fake_download(key, dest_dir):
        path = os.path.join(dest_dir, factors.FACTOR_FILES[key]["file"])
        with open(path, "wb") as f:
            f.write(_zip_bytes(one[key]))
        return path

    monkeypatch.setattr(factors, "_download", fake_download)
    monkeypatch.setattr(factors, "_last_modified",
                        lambda key: "Mon, 03 Aug 2026 19:17:07 GMT")
    rep = factors.update_french_factors(conn, dest_dir=str(dest), force=True)
    assert rep["rows"] == 5
    assert set(rep["downloaded"]) == set(factors.FACTOR_FILES)
    rows = conn.execute(
        "SELECT date, mkt_rf, smb, hml, rmw, cma, mom, st_rev, lt_rev, rf "
        "FROM french_factors ORDER BY date").fetchall()
    assert len(rows) == 5   # the five files' dates are distinct in this fixture
    assert rows[0]["date"] == "1926-01-26" and rows[0]["mkt_rf"] is None
    assert rows[1]["mkt_rf"] == pytest.approx(0.09)   # 1926-07-01 (3F)
    assert rows[3]["lt_rev"] == pytest.approx(-0.30)  # 1930-03-20 (LT)
    assert rows[4]["rmw"] == pytest.approx(-0.01)     # 1963-07-01 (5F)
    # in-DB data dictionary rows for the local table
    nd = conn.execute("SELECT COUNT(*) FROM descriptions "
                      "WHERE table_name='french_factors'").fetchone()[0]
    assert nd == len(factors.FRENCH_DESCRIPTIONS)
    # provenance ledger entry
    s = conn.execute("SELECT source, as_of, row_count FROM snapshots "
                     "WHERE source='french_factors'").fetchone()
    assert s["as_of"] == "1963-07-01" and s["row_count"] == 5


def test_multi_file_merge_preserves_other_files_columns(conn, tmp_path):
    """Regression: the wide table is filled from FIVE source files whose date
    ranges overlap. Sequential INSERT OR REPLACE would NULL out the other
    files' columns on each reload; the upsert must be per-column."""
    f3 = tmp_path / "3f.zip"
    f3.write_bytes(_zip_bytes(",Mkt-RF,SMB,HML,RF\n19260701, 0.09, -0.25, -0.27, 0.01\n"))
    s3 = tmp_path / "st.zip"
    s3.write_bytes(_zip_bytes(",ST_Rev\n19260701, 0.58\n19260702, 0.13\n"))
    spec3 = factors.FACTOR_FILES["3f"]
    specs = factors.FACTOR_FILES["st_rev"]
    bulkload.load_french_factor_file(str(f3), conn, cols=spec3["cols"], vcols=spec3["vcols"])
    bulkload.load_french_factor_file(str(s3), conn, cols=specs["cols"], vcols=specs["vcols"])
    row = conn.execute("SELECT date, mkt_rf, smb, st_rev FROM french_factors "
                       "WHERE date='1926-07-01'").fetchone()
    assert row["mkt_rf"] == pytest.approx(0.09)   # survives the st_rev load
    assert row["st_rev"] == pytest.approx(0.58)
    assert conn.execute("SELECT COUNT(*) FROM french_factors").fetchone()[0] == 2


def test_update_factors_skips_unchanged_files(conn, tmp_path, monkeypatch):
    dest = tmp_path / "bulk"
    os.makedirs(dest, exist_ok=True)
    stamp = "Mon, 03 Aug 2026 19:17:07 GMT"
    manifest = {k: {"modified": stamp} for k in factors.FACTOR_FILES}
    bulk._save_manifest(str(dest), manifest)
    calls = []
    monkeypatch.setattr(factors, "_download",
                        lambda key, dest_dir: calls.append(key) or "x")
    monkeypatch.setattr(factors, "_last_modified", lambda key: stamp)
    rep = factors.update_french_factors(conn, dest_dir=str(dest), force=False)
    assert calls == []                                   # nothing downloaded
    assert set(rep["skipped"]) == set(factors.FACTOR_FILES)
    assert rep["rows"] == 0                              # table stays empty
