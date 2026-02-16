"""
tests/test_database.py - Unit testy pro Database
"""

import os
import tempfile
import pytest

from core.database import Database
from core.config import settings


@pytest.fixture
def temp_db():
    """Dočasná DB pro testování."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        # Dočasně změníme path
        original_path = settings.DB_PATH
        settings.DB_PATH = db_path
        
        db = Database()
        yield db
        
        # Cleanup
        settings.DB_PATH = original_path


def test_database_initialization(temp_db):
    """Test inicializace databáze a tabulky."""
    assert os.path.exists(temp_db.db_path)
    
    # Ověřit že tabulky existují
    conn = temp_db._get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    
    assert "signals" in tables
    assert "trades" in tables
    conn.close()


def test_save_signal(temp_db):
    """Test ukládání signálu."""
    signal_data = {
        "mint": "test_mint_1",
        "symbol": "TEST",
        "liquidity": 5000.0,
        "market_cap": 100000.0,
        "score": 85
    }
    
    temp_db.save_signal(signal_data)
    
    # Ověřit že byl uložen
    conn = temp_db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT mint, symbol, score FROM signals WHERE mint = ?", ("test_mint_1",))
    row = cursor.fetchone()
    conn.close()
    
    assert row is not None
    assert row[0] == "test_mint_1"
    assert row[1] == "TEST"
    assert row[2] == 85


def test_save_trade(temp_db):
    """Test ukládání obchodu."""
    trade_data = {
        "mint": "test_mint_2",
        "entry_price": 0.001,
        "exit_price": 0.002,
        "profit_pct": 100.0,
        "status": "CLOSED_TAKE_PROFIT"
    }
    
    temp_db.save_trade(trade_data)
    
    # Ověřit že byl uložen
    conn = temp_db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT mint, profit_pct, status FROM trades WHERE mint = ?", ("test_mint_2",))
    row = cursor.fetchone()
    conn.close()
    
    assert row is not None
    assert row[0] == "test_mint_2"
    assert row[1] == 100.0
    assert row[2] == "CLOSED_TAKE_PROFIT"


def test_duplicate_signal_ignored(temp_db):
    """Test že duplicitní signály jsou ignorovány."""
    signal_data = {
        "mint": "duplicate_mint",
        "symbol": "DUP",
        "liquidity": 1000.0,
        "market_cap": 50000.0,
        "score": 70
    }
    
    temp_db.save_signal(signal_data)
    temp_db.save_signal(signal_data)  # Pokus o duplikát
    
    # Ověřit že je jen jeden záznam
    conn = temp_db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM signals WHERE mint = ?", ("duplicate_mint",))
    count = cursor.fetchone()[0]
    conn.close()
    
    assert count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
