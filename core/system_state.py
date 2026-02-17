"""
core/system_state.py – Globální stav systému.

Opravy:
- B5: Výchozí balance_sol z VIRTUAL_BALANCE_SOL pro SHADOW/PAPER mód
- Opravený save() – sqlite3.Connection není context manager pro commit
- Bezpečnější load() s fallbackem
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from core.database import db
from core.config import settings

logger = logging.getLogger("Predator.SystemState")


@dataclass
class SystemState:
    mode: str
    status: str  # RUNNING/PAUSED/KILL_SWITCH/ERROR
    balance_sol: float
    solana_reserve: float = 0.005
    daily_pnl: float = 0.0
    open_positions: int = 0
    session_start: datetime = field(default_factory=datetime.utcnow)
    last_update: datetime = field(default_factory=datetime.utcnow)
    error_state: Optional[str] = None

    def save(self):
        """Uloží stav do DB."""
        self.last_update = datetime.utcnow()
        try:
            conn = db.connect()
            data_dict = asdict(self)
            for key in ("session_start", "last_update"):
                if isinstance(data_dict.get(key), datetime):
                    data_dict[key] = data_dict[key].isoformat()
            data = json.dumps(data_dict)
            conn.execute(
                "INSERT OR REPLACE INTO system_state (key, value) VALUES ('current', ?)",
                (data,),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Chyba pri ukladani stavu: {e}")

    @classmethod
    def load(cls) -> "SystemState":
        """Načte stav z DB nebo vytvoří výchozí."""
        try:
            conn = db.connect()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_state WHERE key = 'current'")
            row = cursor.fetchone()
            if row:
                data = json.loads(row[0])
                for key in ("session_start", "last_update"):
                    if key in data and isinstance(data[key], str):
                        data[key] = datetime.fromisoformat(data[key])
                return cls(**data)
        except Exception as e:
            logger.warning(f"Nelze nacist stav z DB, vytvarim vychozi: {e}")

        # B5: Výchozí stav - virtuální balance pro SHADOW/PAPER mód
        mode = settings.SYSTEM_MODE
        balance = settings.VIRTUAL_BALANCE_SOL if mode in ("SHADOW", "PAPER") else 0.0
        return cls(
            mode=mode,
            status="RUNNING",
            balance_sol=balance,
            solana_reserve=settings.SOLANA_RESERVE,
        )


state = SystemState.load()
