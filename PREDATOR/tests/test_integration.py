"""
tests/test_integration.py - Integrační testy pro pipeline
"""

import asyncio
import pytest

from core.event_bus import EventBus
from core.database import Database


@pytest.mark.asyncio
async def test_event_pipeline_miner_hunter():
    """
    Test pipeline: Miner → Hunter
    Simuluje: Miner vydá NEW_COIN_FOUND, Hunter ho zachytí
    """
    bus = EventBus()
    received_signals = []

    async def mock_hunter(payload):
        """Simuluje Hunter na základě signálu od Minera."""
        received_signals.append(payload)

    bus.subscribe("NEW_COIN_FOUND", mock_hunter)

    # Simulace Minera
    miner_payload = {
        "mint": "test_mint_123",
        "name": "Test Token",
        "price": 0.0001,
        "volume_5m": 5000.0,
    }

    await bus.emit("NEW_COIN_FOUND", miner_payload)

    assert len(received_signals) == 1
    assert received_signals[0]["mint"] == "test_mint_123"


@pytest.mark.asyncio
async def test_event_pipeline_analyzer_riskguard():
    """
    Test pipeline: Analyzer → RiskGuard
    Simuluje: Analyzer vydá TRADE_SIGNAL_READY, RiskGuard ho zachytí
    """
    bus = EventBus()
    approved_signals = []
    vetoed_signals = []

    async def mock_riskguard_approve(payload):
        """Odsouhlasí signál."""
        if payload.get("score", 0) >= 80:
            approved_signals.append(payload)

    async def mock_riskguard_veto(payload):
        """Zavrhne signál."""
        if payload.get("score", 0) < 80:
            vetoed_signals.append(payload)

    bus.subscribe("TRADE_SIGNAL_READY", mock_riskguard_approve)
    bus.subscribe("TRADE_SIGNAL_READY", mock_riskguard_veto)

    # Simulace Analyzera - vysoká skóre
    high_score_signal = {
        "token_address": "good_token",
        "score": 85.0,
        "reason": "Good score",
    }

    await bus.emit("TRADE_SIGNAL_READY", high_score_signal)

    assert len(approved_signals) == 1
    assert len(vetoed_signals) == 0

    # Simulace Analyzera - nízká skóre
    low_score_signal = {
        "token_address": "bad_token",
        "score": 60.0,
        "reason": "Low score",
    }

    await bus.emit("TRADE_SIGNAL_READY", low_score_signal)

    assert len(approved_signals) == 1
    assert len(vetoed_signals) == 1


@pytest.mark.asyncio
async def test_event_pipeline_executor_learner():
    """
    Test pipeline: Executor → Learner
    Simuluje: Executor uzavře pozici, Learner zaznamenává data
    """
    bus = EventBus()
    trades_recorded = []

    async def mock_learner(payload):
        """Zaznamenává uzavřené pozice."""
        trades_recorded.append(payload)

    bus.subscribe("POSITION_CLOSED", mock_learner)

    # Simulace Executora - uzavřená pozice
    position_closed = {
        "token_address": "test_token",
        "profit_pct": 25.5,
        "reason": "TAKE_PROFIT_100",
    }

    await bus.emit("POSITION_CLOSED", position_closed)

    assert len(trades_recorded) == 1
    assert trades_recorded[0]["profit_pct"] == 25.5


@pytest.mark.asyncio
async def test_full_pipeline_integration(temp_db):
    """
    End-to-end test: Miner → Hunter → Analyzer → RiskGuard → Executor
    """
    bus = EventBus()
    final_executions = []

    # Simulace Minera
    async def mock_miner():
        await bus.emit("NEW_COIN_FOUND", {
            "mint": "integration_test_mint",
            "name": "Integration Test",
            "price": 0.0001,
            "volume_5m": 10000.0,
            "liquidity": 50000.0,
        })

    # Simulace Huntera
    async def mock_hunter(payload):
        if payload.get("volume_5m", 0) > 5000:
            await bus.emit("GOOD_COIN_SELECTED", payload)

    bus.subscribe("NEW_COIN_FOUND", mock_hunter)

    # Simulace Analyzera
    async def mock_analyzer(payload):
        if payload.get("liquidity", 0) > 10000:
            await bus.emit("TRADE_SIGNAL_READY", {
                "token_address": payload.get("mint"),
                "score": 85.0,
                "amount_sol": 1.0,
            })

    bus.subscribe("GOOD_COIN_SELECTED", mock_analyzer)

    # Simulace RiskGuardu
    async def mock_riskguard(payload):
        if payload.get("score", 0) >= 80:
            await bus.emit("RISK_APPROVED_FOR_EXECUTION", payload)

    bus.subscribe("TRADE_SIGNAL_READY", mock_riskguard)

    # Simulace Executora
    async def mock_executor(payload):
        final_executions.append(payload)

    bus.subscribe("RISK_APPROVED_FOR_EXECUTION", mock_executor)

    # Spustíme pipeline
    await mock_miner()

    # Dáme čas na zpracování event bufferů
    await asyncio.sleep(0.5)

    assert len(final_executions) == 1
    assert final_executions[0]["token_address"] == "integration_test_mint"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
