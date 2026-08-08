"""Shared fixtures for the data-manager test suite."""

import pytest

from data_manager import db


@pytest.fixture
def db_path(tmp_path):
    """Path to a fresh temp SQLite DB (no file exists yet)."""
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_path):
    """An open connection to a fresh temp DB with schema applied."""
    c = db.connect(db_path)
    yield c
    c.close()
