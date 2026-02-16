import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from core.database import db


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
        self.last_update = datetime.utcnow()
        with db.connect() as conn:
            cursor = conn.cursor()
            data_dict = asdict(self)
            # Serialize datetimes to ISO format for JSON
            if isinstance(data_dict.get("session_start"), datetime):
                data_dict["session_start"] = data_dict["session_start"].isoformat()
            if isinstance(data_dict.get("last_update"), datetime):
                data_dict["last_update"] = data_dict["last_update"].isoformat()
            data = json.dumps(data_dict)
            cursor.execute(
                """
                INSERT OR REPLACE INTO system_state (key, value) VALUES ('current', ?)
                """,
                (data,),
            )
            conn.commit()

    @classmethod
    def load(cls) -> "SystemState":
        with db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_state WHERE key = 'current'")
            row = cursor.fetchone()
            if row:
                data = json.loads(row[0])
                data["session_start"] = datetime.fromisoformat(data["session_start"])
                data["last_update"] = datetime.fromisoformat(data["last_update"])
                return cls(**data)
            return cls(mode="SHADOW", status="PAUSED", balance_sol=0.0)


state = SystemState.load()
