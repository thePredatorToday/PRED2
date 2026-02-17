# soubor: modules/risk_guard.py
# Opravy:
# - L2: min_score sjednocen na 80 (dle specifikace)
# - L7: _veto je nyní async (volá bus.emit)
# - Vylepšený drawdown check dle settings
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from core.event_bus import bus
from core.system_state import state
from core.config import settings

logger = logging.getLogger("Predator.RiskGuard")


@dataclass
class ApprovedSignal:
    token_address: str
    amount_sol: float
    approved: bool
    reason: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    original_signal: Dict[str, Any] = field(default_factory=dict)


class RiskGuard:
    def __init__(self):
        self.is_running = False
        self.processed_signals = 0
        self.vetoes = 0
        self.approvals = 0

    async def start(self):
        self.is_running = True
        logger.info("RISKGUARD START – Brana veto aktivovana")

        bus.subscribe("TRADE_SIGNAL_READY", self.check_risk)

        asyncio.create_task(self._heartbeat())

    async def stop(self):
        self.is_running = False
        logger.info("RiskGuard zastaven")

    async def _heartbeat(self):
        while self.is_running:
            await asyncio.sleep(60)
            logger.info(
                f"RiskGuard aktivni | Zpracovano: {self.processed_signals} | "
                f"Schvaleno: {self.approvals} | Veto: {self.vetoes}"
            )

    async def check_risk(self, signal_data: Dict[str, Any]):
        self.processed_signals += 1
        address = signal_data.get("token_address")
        score = signal_data.get("score", 0)

        logger.info(f"Proveruji riziko pro {address} (skore {score:.1f})")

        # 1. Kontrola stavu systému
        if state.status in ["KILL_SWITCH", "ERROR", "PAUSED"]:
            return await self._veto(address, f"System v nouzovem stavu: {state.status}", signal_data)

        # 2. Kontrola denního drawdownu (dle settings)
        drawdown_limit = abs(settings.DAILY_DRAWDOWN_PCT)
        if state.daily_pnl <= -drawdown_limit:
            state.status = "KILL_SWITCH"
            state.save()
            return await self._veto(address, f"Dosazen limit denniho drawdownu (-{drawdown_limit}%)", signal_data)

        # 3. Kontrola počtu otevřených pozic
        if state.open_positions >= settings.MAX_OPEN_POSITIONS:
            return await self._veto(address, f"Dosazen max pocet pozic ({state.open_positions})", signal_data)

        # L2: Kontrola minimálního skóre Analyzera (80 dle specifikace)
        min_score = 80
        if score < min_score:
            return await self._veto(address, f"Nedostatecne skore Analyzera ({score:.1f} < {min_score})", signal_data)

        # 5. Výpočet velikosti pozice
        amount = 0.1 if state.mode == "BETA" else settings.MAX_POSITION_SIZE
        # Dynamické snížení pokud je balanc nízký
        if state.balance_sol < (amount + state.solana_reserve):
            if state.balance_sol > (0.05 + state.solana_reserve):
                amount = 0.05
                logger.warning(f"Snizena velikost pozice na {amount} SOL kvuli nizkemu balancu")
            else:
                return await self._veto(address, f"Nedostatecny balanc ({state.balance_sol:.4f} SOL)", signal_data)

        # 6. Kontrola rezervy
        if (state.balance_sol - amount) < state.solana_reserve:
            return await self._veto(address, "Transakce by narusila SOL rezervu", signal_data)

        # Pokud vše prošlo
        return await self._approve(address, amount, signal_data)

    async def _approve(self, address: str, amount: float, signal_data: Dict[str, Any]):
        self.approvals += 1
        approved_signal = ApprovedSignal(
            token_address=address,
            amount_sol=amount,
            approved=True,
            reason="Vsechny rizikove kontroly v poradku",
            original_signal=signal_data,
        )

        logger.info(f"RISK_APPROVED -> {address} | Pozice: {amount} SOL")
        await bus.emit("RISK_APPROVED_FOR_EXECUTION", approved_signal.__dict__)
        return True

    # L7: _veto je nyní async (předtím sync + create_task pro emit → problém)
    async def _veto(self, address: str, reason: str, signal_data: Dict[str, Any]):
        self.vetoes += 1
        logger.warning(f"RISK_VETO -> {address} | Duvod: {reason}")
        await bus.emit(
            "RISK_VETOED",
            {
                "token_address": address,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
        return False


risk_guard = RiskGuard()
