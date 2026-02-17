"""
core/strategy.py – Centrální konfigurace obchodní strategie.

Tři profily: conservative, default, aggressive.
Moduly si čtou aktivní strategii a reagují na STRATEGY_CHANGE event.

v2.0: Moon Bag, DCA, cooldown, slippage validation, TX simulation, priority fees.
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
    min_holders: int = 10  # min holder count (RPC vraci max 20)
    max_top10_holders_pct: float = 0.50

    # Analyzer
    analyzer_min_score: float = 80.0  # Minimum pro TRADE_SIGNAL_READY

    # RiskGuard
    risk_min_score: float = 80.0
    max_position_size: float = 2.0
    max_open_positions: int = 3
    cooldown_seconds: int = 30  # Min pauza mezi nakupy

    # Executor
    stop_loss_pct: float = -15.0
    take_profit_pct: float = 100.0
    trailing_trigger_pct: float = 20.0
    trailing_drop_pct: float = 20.0

    # Moon Bag: prodej X% na TP, zbytek drzi
    moon_bag_enabled: bool = True
    moon_bag_sell_pct: float = 80.0  # prodat 80% na 2x
    moon_bag_trigger_pct: float = 100.0  # trigger = +100% (2x)
    moon_bag_trailing_pct: float = 40.0  # trailing stop pro moon bag cast

    # DCA (Dollar Cost Averaging)
    dca_enabled: bool = True
    dca_max_entries: int = 3  # max DCA vstupu na token
    dca_dip_trigger_pct: float = -20.0  # prikoupeni po -20% dipu
    dca_multiplier: float = 0.5  # DCA pozice = 50% originalni

    # Slippage
    slippage_bps: int = 500  # 5%
    max_price_impact_pct: float = 3.0  # max price impact
    slippage_tolerance_pct: float = 2.0  # max odchylka od quote

    # TX
    use_tx_simulation: bool = True  # simulace pred odeslanim
    priority_fee_mode: str = "dynamic"  # "static", "dynamic", "none"
    priority_fee_max_lamports: int = 100000


# Preddefinovane profily
PROFILES: Dict[str, StrategyProfile] = {
    "conservative": StrategyProfile(
        name="conservative",
        min_liquidity=5000.0,
        min_volume_24h=3000.0,
        min_prelim_score=30.0,
        min_final_score=85.0,
        min_holders=15,
        max_top10_holders_pct=0.40,
        analyzer_min_score=85.0,
        risk_min_score=85.0,
        max_position_size=1.0,
        max_open_positions=2,
        cooldown_seconds=60,
        stop_loss_pct=-10.0,
        take_profit_pct=80.0,
        trailing_trigger_pct=15.0,
        trailing_drop_pct=15.0,
        moon_bag_enabled=True,
        moon_bag_sell_pct=90.0,
        moon_bag_trigger_pct=80.0,
        moon_bag_trailing_pct=30.0,
        dca_enabled=False,
        dca_max_entries=2,
        dca_dip_trigger_pct=-25.0,
        dca_multiplier=0.3,
        slippage_bps=300,
        max_price_impact_pct=2.0,
        slippage_tolerance_pct=1.5,
        use_tx_simulation=True,
        priority_fee_mode="dynamic",
        priority_fee_max_lamports=50000,
    ),
    "default": StrategyProfile(
        name="default",
        min_liquidity=2000.0,
        min_volume_24h=1000.0,
        min_prelim_score=15.0,
        min_final_score=75.0,
        min_holders=10,
        max_top10_holders_pct=0.50,
        analyzer_min_score=80.0,
        risk_min_score=80.0,
        max_position_size=2.0,
        max_open_positions=3,
        cooldown_seconds=30,
        stop_loss_pct=-15.0,
        take_profit_pct=100.0,
        trailing_trigger_pct=20.0,
        trailing_drop_pct=20.0,
        moon_bag_enabled=True,
        moon_bag_sell_pct=80.0,
        moon_bag_trigger_pct=100.0,
        moon_bag_trailing_pct=40.0,
        dca_enabled=True,
        dca_max_entries=3,
        dca_dip_trigger_pct=-20.0,
        dca_multiplier=0.5,
        slippage_bps=500,
        max_price_impact_pct=3.0,
        slippage_tolerance_pct=2.0,
        use_tx_simulation=True,
        priority_fee_mode="dynamic",
        priority_fee_max_lamports=100000,
    ),
    "aggressive": StrategyProfile(
        name="aggressive",
        min_liquidity=1000.0,
        min_volume_24h=500.0,
        min_prelim_score=10.0,
        min_final_score=60.0,
        min_holders=5,
        max_top10_holders_pct=0.60,
        analyzer_min_score=70.0,
        risk_min_score=70.0,
        max_position_size=3.0,
        max_open_positions=5,
        cooldown_seconds=15,
        stop_loss_pct=-20.0,
        take_profit_pct=150.0,
        trailing_trigger_pct=30.0,
        trailing_drop_pct=25.0,
        moon_bag_enabled=True,
        moon_bag_sell_pct=70.0,
        moon_bag_trigger_pct=150.0,
        moon_bag_trailing_pct=50.0,
        dca_enabled=True,
        dca_max_entries=4,
        dca_dip_trigger_pct=-15.0,
        dca_multiplier=0.7,
        slippage_bps=800,
        max_price_impact_pct=5.0,
        slippage_tolerance_pct=3.0,
        use_tx_simulation=True,
        priority_fee_mode="dynamic",
        priority_fee_max_lamports=200000,
    ),
}


class StrategyManager:
    """Singleton spravce aktivni strategie."""

    def __init__(self):
        self.active: StrategyProfile = PROFILES["default"]

    def set_strategy(self, name: str) -> bool:
        """Zmeni aktivni strategii. Vraci True pokud se zmenila."""
        if name not in PROFILES:
            logger.warning(f"Neznama strategie: {name}")
            return False
        self.active = PROFILES[name]
        logger.info(f"Strategie zmenena na: {name}")
        return True

    def get(self) -> StrategyProfile:
        return self.active


strategy = StrategyManager()
