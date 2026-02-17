"""
core/strategy.py – Centrální konfigurace obchodní strategie.

Tři profily: conservative, default, aggressive.
Moduly si čtou aktivní strategii a reagují na STRATEGY_CHANGE event.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict

logger = logging.getLogger("Predator.Strategy")


@dataclass
class StrategyProfile:
    """Parametry obchodní strategie."""

    name: str

    # Hunter thresholds
    min_liquidity: float = 2000.0
    min_volume_24h: float = 1000.0
    min_prelim_score: float = 15.0  # Hunter preliminary
    min_final_score: float = 75.0  # Hunter final (po RPC)

    # Analyzer
    analyzer_min_score: float = 80.0  # Minimum pro TRADE_SIGNAL_READY

    # RiskGuard
    risk_min_score: float = 80.0
    max_position_size: float = 2.0
    max_open_positions: int = 3

    # Executor
    stop_loss_pct: float = -15.0
    take_profit_pct: float = 100.0
    trailing_trigger_pct: float = 20.0
    trailing_drop_pct: float = 20.0

    # Slippage
    slippage_bps: int = 500  # 5%


# Předdefinované profily
PROFILES: Dict[str, StrategyProfile] = {
    "conservative": StrategyProfile(
        name="conservative",
        min_liquidity=5000.0,
        min_volume_24h=3000.0,
        min_prelim_score=30.0,
        min_final_score=85.0,
        analyzer_min_score=85.0,
        risk_min_score=85.0,
        max_position_size=1.0,
        max_open_positions=2,
        stop_loss_pct=-10.0,
        take_profit_pct=80.0,
        trailing_trigger_pct=15.0,
        trailing_drop_pct=15.0,
        slippage_bps=300,
    ),
    "default": StrategyProfile(
        name="default",
        min_liquidity=2000.0,
        min_volume_24h=1000.0,
        min_prelim_score=15.0,
        min_final_score=75.0,
        analyzer_min_score=80.0,
        risk_min_score=80.0,
        max_position_size=2.0,
        max_open_positions=3,
        stop_loss_pct=-15.0,
        take_profit_pct=100.0,
        trailing_trigger_pct=20.0,
        trailing_drop_pct=20.0,
        slippage_bps=500,
    ),
    "aggressive": StrategyProfile(
        name="aggressive",
        min_liquidity=1000.0,
        min_volume_24h=500.0,
        min_prelim_score=10.0,
        min_final_score=60.0,
        analyzer_min_score=70.0,
        risk_min_score=70.0,
        max_position_size=3.0,
        max_open_positions=5,
        stop_loss_pct=-20.0,
        take_profit_pct=150.0,
        trailing_trigger_pct=30.0,
        trailing_drop_pct=25.0,
        slippage_bps=800,
    ),
}


class StrategyManager:
    """Singleton správce aktivní strategie."""

    def __init__(self):
        self.active: StrategyProfile = PROFILES["default"]

    def set_strategy(self, name: str) -> bool:
        """Změní aktivní strategii. Vrací True pokud se změnila."""
        if name not in PROFILES:
            logger.warning(f"Neznama strategie: {name}")
            return False
        self.active = PROFILES[name]
        logger.info(f"Strategie zmenena na: {name}")
        return True

    def get(self) -> StrategyProfile:
        return self.active


strategy = StrategyManager()
