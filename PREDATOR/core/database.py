"""
core/database.py – SQLite databázová vrstva pro PREDATOR.

Synchronní wrapper nad sqlite3 s thread-safe přístupem.
Tabulky: signals, trades, predictions, commands, module_reports,
         coin_updates, learner_suggestions, system_state, event_log.
"""

import logging
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from core.config import settings

logger = logging.getLogger("Predator.Database")


class Database:
    """Thread-safe SQLite databáze."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.DB_PATH
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Vrátí thread-local spojení."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    def connect(self) -> sqlite3.Connection:
        """Veřejná metoda pro přímý přístup."""
        return self._get_connection()

    def _init_db(self) -> None:
        """Vytvoří tabulky pokud neexistují."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint TEXT UNIQUE,
                    symbol TEXT,
                    liquidity REAL,
                    market_cap REAL,
                    score INTEGER,
                    status TEXT DEFAULT 'NEW',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    profit_pct REAL,
                    status TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint TEXT,
                    symbol TEXT,
                    predicted_mcap REAL,
                    predicted_price REAL,
                    predicted_at TIMESTAMP,
                    followup_sent INTEGER DEFAULT 0,
                    actual_mcap REAL,
                    actual_price REAL,
                    result TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT,
                    payload TEXT,
                    processed INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS module_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module TEXT,
                    status TEXT,
                    metrics TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS coin_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint TEXT,
                    symbol TEXT,
                    update_type TEXT,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS learner_suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint TEXT,
                    suggestion TEXT,
                    score REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Event log pro dashboard - bohaté logy systému
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'INFO',
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Indexy
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_signals_mint ON signals(mint)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_mint ON trades(mint)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_commands_processed ON commands(processed)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_log_created ON event_log(created_at)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_log_module ON event_log(module)"
            )

            conn.commit()
            logger.info("Databaze uspesne inicializovana.")
        except Exception as e:
            logger.critical(f"Chyba pri inicializaci databaze: {e}")
            raise

    # --- Event Log ---

    def log_event(
        self,
        module: str,
        event_type: str,
        message: str,
        level: str = "INFO",
        details: Optional[str] = None,
    ) -> None:
        """Zapíše událost do event_log tabulky pro dashboard."""
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO event_log (module, level, event_type, message, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (module, level, event_type, message, details),
            )
            conn.commit()
        except Exception:
            pass  # Nesmí blokovat hlavní logiku

    def fetch_event_log(self, limit: int = 200, module: Optional[str] = None) -> List[Tuple]:
        """Vrátí poslední události z event_log."""
        try:
            conn = self._get_connection()
            if module:
                cursor = conn.execute(
                    "SELECT id, module, level, event_type, message, details, created_at "
                    "FROM event_log WHERE module=? ORDER BY id DESC LIMIT ?",
                    (module, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT id, module, level, event_type, message, details, created_at "
                    "FROM event_log ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            return cursor.fetchall()
        except Exception:
            return []

    def cleanup_old_events(self, keep_hours: int = 24) -> None:
        """Smaže staré event_log záznamy."""
        try:
            conn = self._get_connection()
            conn.execute(
                "DELETE FROM event_log WHERE created_at < datetime('now', ?)",
                (f"-{keep_hours} hours",),
            )
            conn.commit()
        except Exception:
            pass

    # --- Signals ---

    def save_signal(self, data: Dict[str, Any]) -> None:
        """Uloží nový signál."""
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT OR IGNORE INTO signals (mint, symbol, liquidity, market_cap, score) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    data.get("mint"),
                    data.get("symbol"),
                    data.get("liquidity"),
                    data.get("market_cap"),
                    data.get("score"),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Chyba pri ukladani signalu: {e}")

    # --- Trades ---

    def save_trade(self, data: Dict[str, Any]) -> None:
        """Uloží záznam o obchodu."""
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO trades (mint, entry_price, exit_price, profit_pct, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    data.get("mint"),
                    data.get("entry_price"),
                    data.get("exit_price"),
                    data.get("profit_pct"),
                    data.get("status"),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Chyba pri ukladani obchodu: {e}")

    def get_recent_trades(self, limit: int = 50) -> List[Tuple]:
        """Vrátí poslední obchody."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT id, mint, entry_price, exit_price, profit_pct, status, timestamp "
                "FROM trades ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"Chyba pri cteni obchodu: {e}")
            return []

    # --- Predictions ---

    def save_prediction(self, data: Dict[str, Any]) -> int:
        """Uloží předpověď a vrátí ID záznamu."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(
                "INSERT INTO predictions (mint, symbol, predicted_mcap, predicted_price, predicted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    data.get("mint"),
                    data.get("symbol"),
                    data.get("predicted_mcap"),
                    data.get("predicted_price"),
                    data.get("predicted_at"),
                ),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"Chyba pri ukladani predikce: {e}")
            return -1

    def update_prediction_result(self, prediction_id: int, result: Dict[str, Any]) -> None:
        """Aktualizuje výsledek predikce."""
        try:
            conn = self._get_connection()
            conn.execute(
                "UPDATE predictions SET followup_sent=1, actual_mcap=?, actual_price=?, result=? WHERE id=?",
                (
                    result.get("actual_mcap"),
                    result.get("actual_price"),
                    result.get("result"),
                    prediction_id,
                ),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Chyba pri aktualizaci predikce: {e}")

    # --- Commands ---

    def push_command(self, action: str, payload: str) -> int:
        """Vloží příkaz do fronty."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(
                "INSERT INTO commands (action, payload) VALUES (?, ?)",
                (action, payload),
            )
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"Chyba pri push command: {e}")
            return -1

    def fetch_unprocessed_commands(self) -> List[Tuple]:
        """Vrátí nezpracované příkazy."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT id, action, payload FROM commands WHERE processed=0 ORDER BY id ASC"
            )
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"Chyba pri cteni commands: {e}")
            return []

    def mark_command_processed(self, command_id: int) -> None:
        """Označí příkaz jako zpracovaný."""
        try:
            conn = self._get_connection()
            conn.execute("UPDATE commands SET processed=1 WHERE id=?", (command_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Chyba pri oznacovani command: {e}")

    # --- Module Reports ---

    def save_module_report(self, module: str, status: str, metrics: str) -> None:
        """Uloží report modulu."""
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO module_reports (module, status, metrics) VALUES (?, ?, ?)",
                (module, status, metrics),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Chyba pri ukladani module report: {e}")

    # --- Coin Updates ---

    def save_coin_update(self, data: Dict[str, Any]) -> None:
        """Uloží aktualizaci coinu."""
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO coin_updates (mint, symbol, update_type, details) "
                "VALUES (?, ?, ?, ?)",
                (
                    data.get("mint"),
                    data.get("symbol"),
                    data.get("update_type"),
                    data.get("details"),
                ),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Chyba pri ukladani coin update: {e}")

    def fetch_recent_coin_updates(self, limit: int = 50) -> List[Tuple]:
        """Vrátí poslední aktualizace coinů."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT id, mint, symbol, update_type, details, created_at "
                "FROM coin_updates ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return cursor.fetchall()
        except Exception:
            return []

    # --- Learner Suggestions ---

    def save_learner_suggestion(self, data: Dict[str, Any]) -> None:
        """Uloží návrh od Learner modulu."""
        try:
            conn = self._get_connection()
            conn.execute(
                "INSERT INTO learner_suggestions (mint, suggestion, score) VALUES (?, ?, ?)",
                (data.get("mint"), data.get("suggestion"), data.get("score")),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Chyba pri ukladani learner suggestion: {e}")

    def fetch_recent_learner_suggestions(self, limit: int = 50) -> List[Tuple]:
        """Vrátí poslední návrhy od Learneru."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(
                "SELECT id, mint, suggestion, score, created_at "
                "FROM learner_suggestions ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return cursor.fetchall()
        except Exception:
            return []


# Singleton
db = Database()
