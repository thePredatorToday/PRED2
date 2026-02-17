"""
tests/test_system_state.py - Unit testy pro SystemState
"""

import pytest
from datetime import datetime

from core.system_state import SystemState


def test_system_state_creation():
    """Test vytvoření SystemState."""
    state = SystemState(
        mode="SHADOW",
        status="RUNNING",
        balance_sol=1.5,
    )
    
    assert state.mode == "SHADOW"
    assert state.status == "RUNNING"
    assert state.balance_sol == 1.5
    assert state.solana_reserve == 0.005
    assert state.open_positions == 0
    assert state.daily_pnl == 0.0


def test_system_state_defaults():
    """Test defaultní hodnoty."""
    state = SystemState(
        mode="LIVE",
        status="PAUSED",
        balance_sol=10.0,
    )
    
    assert state.solana_reserve == 0.005
    assert state.daily_pnl == 0.0
    assert state.open_positions == 0
    assert state.error_state is None


def test_system_state_modifications():
    """Test modifikace stavu."""
    state = SystemState(
        mode="PAPER",
        status="RUNNING",
        balance_sol=5.0,
    )
    
    # Simulujeme změny
    state.balance_sol -= 0.5
    state.open_positions += 1
    state.daily_pnl += 10.5
    state.status = "PAUSED"
    state.error_state = "RPC Timeout"
    
    assert state.balance_sol == 4.5
    assert state.open_positions == 1
    assert state.daily_pnl == 10.5
    assert state.status == "PAUSED"
    assert state.error_state == "RPC Timeout"


def test_system_state_reserve_protection():
    """Test ochrana minimální rezervy."""
    state = SystemState(
        mode="BETA",
        status="RUNNING",
        balance_sol=0.010,
    )
    
    reserve = state.solana_reserve
    available = state.balance_sol - reserve
    
    # Simulujeme kontrolu dostupných prostředků
    assert available >= 0  # Reserve musí být vždy chráněná
    assert state.balance_sol >= reserve


def test_system_state_emergency_states():
    """Test nouzové stavy."""
    state = SystemState(
        mode="LIVE",
        status="RUNNING",
        balance_sol=1.0,
    )
    
    # Simulace kill switch
    state.status = "KILL_SWITCH"
    state.error_state = "Daily drawdown exceeded"
    
    assert state.status == "KILL_SWITCH"
    assert state.error_state is not None
    # V reálném app by se teď prodávalo vše

