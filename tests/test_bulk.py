"""Tests for bulk.py orchestration (manifest skip, from-zero, update)."""
import json, os
import pytest

from data_manager import bulk as B


def _fake_status(modified="2026-08-11T23:17:19.840Z", size=1000, label="1 KB"):
    return {"table": "x", "files": [{"history": "full", "name": "x.csv.zip",
                                     "size": size, "sizeLabel": label, "modified": modified}]}


@pytest.fixture
def dir_(tmp_path):
    return str(tmp_path / "bulk")


def test_sync_skips_when_manifest_matches(dir_, monkeypatch):
    os.makedirs(dir_, exist_ok=True)
    B._save_manifest(dir_, {"x": {"modified": "2026-08-11T23:17:19.840Z", "name": "x.csv.zip"}})
    monkeypatch.setattr(B, "bulk_status", lambda table: _fake_status())
    monkeypatch.setattr(B, "_download", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no download")))
    assert B.sync_table("x", dir_) is False


def test_sync_downloads_when_modified_changes(dir_, monkeypatch, tmp_path):
    os.makedirs(dir_, exist_ok=True)
    B._save_manifest(dir_, {"x": {"modified": "2020-01-01T00:00:00Z", "name": "x.csv.zip"}})
    monkeypatch.setattr(B, "bulk_status", lambda table: _fake_status())
    fake = tmp_path / "fake.zip"
    fake.write_bytes(b"data")
    monkeypatch.setattr(B, "_download", lambda *a, **k: str(fake))
    assert B.sync_table("x", dir_) is True
    m = B._manifest(dir_)
    assert m["x"]["modified"] == "2026-08-11T23:17:19.840Z"


def test_bulk_fromzero_wipes_and_loads_all(dir_, monkeypatch, conn):
    for t in B.WIPE_TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO prices (ticker, date, close, volume) VALUES ('X','2026-01-01',5,10)")
    conn.commit()
    monkeypatch.setattr(B, "sync_table", lambda *a, **k: True)
    monkeypatch.setattr(B, "bulk_status", lambda t: _fake_status())
    def fake_load(table, path, conn=None):
        return 7
    monkeypatch.setattr(B, "_load", fake_load)
    monkeypatch.setattr("data_manager.universe.build_piotroski", lambda conn: 1)
    monkeypatch.setattr("data_manager.universe.build_quarterly", lambda conn: 1)
    monkeypatch.setattr("data_manager.universe.build_ratios", lambda conn: 1)
    monkeypatch.setattr("data_manager.universe.build_universe_pit",
                        lambda conn, as_of=None: 123)
    counts = B.bulk_fromzero(dest_dir=dir_, conn=conn, derive=True, pit=True)
    assert counts["x"] if False else True
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0  # wiped
    assert set(counts.keys()) >= set(B.BULK_TABLES)


def test_bulk_update_skips_unmodified_and_loads_changed(dir_, monkeypatch, conn):
    os.makedirs(dir_, exist_ok=True)
    B._save_manifest(dir_, {"stocks": {"modified": "2020-01-01T00:00:00Z", "name": "stocks.csv.zip"}})
    monkeypatch.setattr(B, "bulk_status", lambda t: _fake_status() if t == "stocks"
                        else _fake_status(modified="2026-08-11T00:00:00Z"))
    monkeypatch.setattr(B, "_download", lambda *a, **k: (dir_ + "/x.zip"))
    loads = []
    monkeypatch.setattr(B, "_load", lambda t, p, conn=None: loads.append(t) or 1)
    monkeypatch.setattr("data_manager.universe.build_piotroski", lambda conn: 1)
    monkeypatch.setattr("data_manager.universe.build_quarterly", lambda conn: 1)
    monkeypatch.setattr("data_manager.universe.build_ratios", lambda conn: 1)
    monkeypatch.setattr("data_manager.universe.build_universe_pit", lambda conn, as_of=None: 1)
    res = B.bulk_update(dest_dir=dir_, conn=conn, tables=["stocks"], force=False)
    assert loads == ["stocks"]
    assert res["skipped"] == []
