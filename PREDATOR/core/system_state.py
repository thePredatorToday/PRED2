"""
core/system_state.py – Globalni stav systemu.

v2.0: asyncio.Lock pro thread-safe aktualizace balance/positions.
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from core.database import db
from core.config import settings

logger = logging.getLogger("Predator.SystemState")

# Globalni lock pro atomicke operace se stavem
_state_lock = asyncio.Lock()


def get_state_lock() -> asyncio.Lock:
    """Vrati globalni state lock pro pouziti v modulech."""
    return _state_lock


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
        """Ulozi stav do DB."""
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

    async def update_balance(self, delta_sol: float, reason: str = ""):
        """Atomicka aktualizace balance s lockem."""
        async with _state_lock:
            self.balance_sol += delta_sol
            self.save()
            if reason:
                logger.debug(f"Balance update: {delta_sol:+.4f} SOL ({reason}) -> {self.balance_sol:.4f}")

    async def update_positions(self, count: int):
        """Atomicka aktualizace poctu pozic."""
        async with _state_lock:
            self.open_positions = count
            self.save()

    async def update_pnl(self, pnl_delta: float):
        """Atomicka aktualizace daily PnL."""
        async with _state_lock:
            self.daily_pnl += pnl_delta
            self.save()

    @classmethod
    def load(cls) -> "SystemState":
        """Nacte stav z DB nebo vytvori vychozi."""
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

        mode = settings.SYSTEM_MODE
        balance = settings.VIRTUAL_BALANCE_SOL if mode in ("SHADOW", "PAPER") else 0.0
        return cls(
            mode=mode,
            status="RUNNING",
            balance_sol=balance,
            solana_reserve=settings.SOLANA_RESERVE,
        )


state = SystemState.load()
