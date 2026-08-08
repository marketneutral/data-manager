"""Tests for data_manager.db — SQLite storage layer."""

import sqlite3

from data_manager import db

TABLES = {
    "universe",
    "prices",
    "classifications",
    "snapshots",
    "quarterly_statements",
    "ratios",
    "fundamentals",
}


def test_connect_creates_all_tables(db_path):
    conn = db.connect(db_path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    conn.close()


def test_connect_is_idempotent(db_path):
    db.connect(db_path).close()
    conn = db.connect(db_path)  # must not raise on an existing DB
    conn.close()


def test_connect_sets_wal_mode(db_path):
    conn = db.connect(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()


def test_connect_sets_busy_timeout(db_path):
    conn = db.connect(db_path)
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout == 20000
    conn.close()


def test_connect_migrates_legacy_schema(db_path):
    """A DB created before the enrichment columns must be migrated in place."""
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        CREATE TABLE universe (
            ticker TEXT PRIMARY KEY, name TEXT, source TEXT, added_at TEXT
        );
        CREATE TABLE prices (
            ticker TEXT, date TEXT, open REAL, high REAL, low REAL,
            close REAL, volume INTEGER, PRIMARY KEY (ticker, date)
        );
    """)
    raw.commit()
    raw.close()

    conn = db.connect(db_path)
    ucols = {r[1] for r in conn.execute("PRAGMA table_info(universe)")}
    assert {"figi", "cik", "sic", "sic_description", "lei"} <= ucols
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(prices)")}
    assert "adjustment" in pcols
    conn.close()


def test_connect_migration_preserves_existing_rows(db_path):
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        CREATE TABLE universe (
            ticker TEXT PRIMARY KEY, name TEXT, source TEXT, added_at TEXT
        );
        INSERT INTO universe VALUES ('AAPL', 'Apple', 'IWV', '2026-01-01');
    """)
    raw.commit()
    raw.close()

    conn = db.connect(db_path)
    row = conn.execute("SELECT ticker, name FROM universe").fetchone()
    assert row["ticker"] == "AAPL"
    assert row["name"] == "Apple"
    conn.close()


def test_default_db_expands_user_home():
    assert "~" not in str(db.DEFAULT_DB)
    assert db.DEFAULT_DB.is_absolute()
