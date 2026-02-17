"""
tests/conftest.py – Sdílené fixtures pro testy.
"""

import os
import tempfile

import pytest

from core.config import settings
from core.database import Database
from core.event_bus import EventBus


@pytest.fixture
def temp_db():
    """Dočasná SQLite DB pro testování."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        original = settings.DB_PATH
        settings.DB_PATH = db_path
        database = Database(db_path=db_path)
        yield database
        try:
            # close any open connection to allow cleanup on Windows
            conn = database.connect()
            conn.close()
        except Exception:
            pass
        settings.DB_PATH = original


@pytest.fixture
def test_bus():
    """Čistý EventBus pro testování."""
    return EventBus()
