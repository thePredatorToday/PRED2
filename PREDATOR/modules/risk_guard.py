# soubor: modules/risk_guard.py
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
        logger.info("🛡️ RISKGUARD START – Brána veto aktivována")
        
        # Odebíráme od Analyzera
        bus.subscribe("TRADE_SIGNAL_READY", self.check_risk)
        
        asyncio.create_task(self._heartbeat())

    async def stop(self):
        self.is_running = False
        logger.info("🛑 RiskGuard zastaven")

    async def _heartbeat(self):
        while self.is_running:
            await asyncio.sleep(60)
            logger.info(
                f"🛡️ RiskGuard aktivní | Zpracováno: {self.processed_signals} | "
                f"Schváleno: {self.approvals} | Veto: {self.vetoes}"
            )

    async def check_risk(self, signal_data: Dict[str, Any]):
        self.processed_signals += 1
        address = signal_data.get("token_address")
        score = signal_data.get("score", 0)
        
        logger.info(f"🛡️ Prověřuji riziko pro {address} (skóre {score:.1f})")

        # 1. Kontrola stavu systému
        if state.status in ["KILL_SWITCH", "ERROR", "PAUSED"]:
            return self._veto(address, f"Systém v nouzovém stavu: {state.status}", signal_data)

        # 2. Kontrola denního drawdownu (-30%)
        if state.daily_pnl <= -30.0:
            state.status = "KILL_SWITCH"
            state.save()
            return self._veto(address, "Dosažen limit denního drawdownu (-30%)", signal_data)

        # 3. Kontrola počtu otevřených pozic (Max 3)
        if state.open_positions >= 3:
            return self._veto(address, f"Dosažen max počet pozic ({state.open_positions})", signal_data)

        # 4. Kontrola minimálního skóre Analyzera (80+ dle specifikace)
        if score < 80:
             return self._veto(address, f"Nedostatečné skóre Analyzera ({score:.1f})", signal_data)

        # 5. Výpočet velikosti pozice (Beta limit 0.1 SOL, jinak max 2 SOL)
        amount = 0.1 if state.mode == "BETA" else 2.0
        # Dynamické snížení pokud je balanc nízký
        if state.balance_sol < (amount + state.solana_reserve):
            # Zkusíme menší velikost nebo zamítneme
            if state.balance_sol > (0.05 + state.solana_reserve):
                amount = 0.05
                logger.warning(f"Snížena velikost pozice na {amount} SOL kvůli nízkému balancu")
            else:
                return self._veto(address, f"Nedostatečný balanc ({state.balance_sol:.4f} SOL)", signal_data)

        # 6. Kontrola rezervy
        if (state.balance_sol - amount) < state.solana_reserve:
             return self._veto(address, "Transakce by narušila SOL rezervu", signal_data)

        # Pokud vše prošlo
        return await self._approve(address, amount, signal_data)

    async def _approve(self, address: str, amount: float, signal_data: Dict[str, Any]):
        self.approvals += 1
        approved_signal = ApprovedSignal(
            token_address=address,
            amount_sol=amount,
            approved=True,
            reason="Všechny rizikové kontroly v pořádku",
            original_signal=signal_data
        )
        
        logger.info(f"✅ RISK_APPROVED → {address} | Pozice: {amount} SOL")
        await bus.emit("RISK_APPROVED_FOR_EXECUTION", approved_signal.__dict__)
        return True

    def _veto(self, address: str, reason: str, signal_data: Dict[str, Any]):
        self.vetoes += 1
        logger.warning(f"⛔ RISK_VETO → {address} | Důvod: {reason}")
        # Veto signály můžeme emitovat pro Dashboard
        async def emit_veto():
            await bus.emit("RISK_VETOED", {
                "token_address": address,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat()
            })
        asyncio.create_task(emit_veto())
        return False

risk_guard = RiskGuard()
