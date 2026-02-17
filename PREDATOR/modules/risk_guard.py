# soubor: modules/risk_guard.py
# v2.0: Cooldown mezi obchody, portfolio drawdown, vylepsene veto

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from core.event_bus import bus
from core.system_state import state
from core.config import settings
from core.database import db
from core.strategy import strategy

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
        # Cooldown tracking
        self.last_trade_time: float = 0.0
        # Portfolio drawdown
        self.peak_balance: float = 0.0
        self.starting_balance: float = 0.0

    async def start(self):
        self.is_running = True
        self.starting_balance = state.balance_sol
        self.peak_balance = state.balance_sol
        logger.info("RISKGUARD START")

        bus.subscribe("TRADE_SIGNAL_READY", self.check_risk)
        bus.subscribe("STRATEGY_CHANGE", self._on_strategy_change)
        bus.subscribe("POSITION_CLOSED", self._on_position_closed)

        asyncio.create_task(self._heartbeat())
        db.log_event("RiskGuard", "MODULE_START", "RiskGuard spusten")

    async def stop(self):
        self.is_running = False
        logger.info("RiskGuard zastaven")

    async def _heartbeat(self):
        while self.is_running:
            await asyncio.sleep(60)
            s = strategy.get()
            drawdown = self._get_portfolio_drawdown()
            logger.info(
                f"RiskGuard | Zpracovano: {self.processed_signals} | "
                f"Schvaleno: {self.approvals} | Veto: {self.vetoes} | "
                f"Drawdown: {drawdown:.2f}% | Min score: {s.risk_min_score}"
            )

    async def _on_strategy_change(self, payload: Dict[str, Any]):
        name = payload.get("strategy", "default")
        strategy.set_strategy(name)
        s = strategy.get()
        db.log_event(
            "RiskGuard", "STRATEGY_CHANGE",
            f"Strategie: {s.name} | min_score={s.risk_min_score} | "
            f"max_pos={s.max_position_size} SOL | max_open={s.max_open_positions} | "
            f"cooldown={s.cooldown_seconds}s",
        )

    async def _on_position_closed(self, payload: Dict[str, Any]):
        """Aktualizuje peak balance po zavreni pozice."""
        if state.balance_sol > self.peak_balance:
            self.peak_balance = state.balance_sol

    def _get_portfolio_drawdown(self) -> float:
        """Skutecny portfolio drawdown od peak balance (v SOL)."""
        if self.peak_balance == 0:
            return 0.0
        return ((state.balance_sol - self.peak_balance) / self.peak_balance) * 100

    async def check_risk(self, signal_data: Dict[str, Any]):
        self.processed_signals += 1
        address = signal_data.get("token_address")
        score = signal_data.get("score", 0)
        s = strategy.get()

        logger.info(f"Risk check: {address} (score {score:.1f})")

        # 1. System status
        if state.status in ["KILL_SWITCH", "ERROR", "PAUSED"]:
            return await self._veto(address, f"System stav: {state.status}", signal_data)

        # 2. Portfolio drawdown (FIX: skutecny drawdown misto sumy %)
        drawdown = self._get_portfolio_drawdown()
        drawdown_limit = abs(settings.DAILY_DRAWDOWN_PCT)
        if drawdown <= -drawdown_limit:
            state.status = "KILL_SWITCH"
            state.save()
            db.log_event(
                "RiskGuard", "KILL_SWITCH",
                f"KILL SWITCH aktivovan! Drawdown: {drawdown:.2f}% (limit: -{drawdown_limit}%)",
                level="ERROR",
            )
            return await self._veto(address, f"Portfolio drawdown {drawdown:.2f}% (limit -{drawdown_limit}%)", signal_data)

        # 3. Cooldown mezi obchody
        now = time.time()
        elapsed = now - self.last_trade_time
        if elapsed < s.cooldown_seconds and self.last_trade_time > 0:
            remaining = s.cooldown_seconds - elapsed
            return await self._veto(
                address,
                f"Cooldown aktivni ({remaining:.0f}s zbyvajicich z {s.cooldown_seconds}s)",
                signal_data,
            )

        # 4. Max pozic (ze strategie)
        if state.open_positions >= s.max_open_positions:
            return await self._veto(address, f"Max pozic ({state.open_positions}/{s.max_open_positions})", signal_data)

        # 5. Min skore (ze strategie)
        if score < s.risk_min_score:
            return await self._veto(address, f"Score {score:.1f} < {s.risk_min_score}", signal_data)

        # 6. Velikost pozice (ze strategie)
        amount = 0.1 if state.mode == "BETA" else s.max_position_size
        if state.balance_sol < (amount + state.solana_reserve):
            if state.balance_sol > (0.05 + state.solana_reserve):
                amount = 0.05
            else:
                return await self._veto(address, f"Low balance ({state.balance_sol:.4f} SOL)", signal_data)

        # 7. Rezerva
        if (state.balance_sol - amount) < state.solana_reserve:
            return await self._veto(address, "SOL rezerva by byla narusena", signal_data)

        # 8. Koncentrace pozic (max 50% balancu v pozicich)
        total_in_positions = sum(
            p.get("amount_sol", 0)
            for p in self._get_executor_positions().values()
        )
        if total_in_positions + amount > state.balance_sol * 0.8:
            return await self._veto(
                address,
                f"Koncentrace pozic prilis vysoka ({total_in_positions + amount:.2f} > 80% balancu)",
                signal_data,
            )

        return await self._approve(address, amount, signal_data)

    def _get_executor_positions(self) -> Dict[str, Any]:
        """Bezpecne ziska aktivni pozice z executoru."""
        try:
            from modules.executor import executor
            return executor.active_positions
        except Exception:
            return {}

    async def _approve(self, address: str, amount: float, signal_data: Dict[str, Any]):
        self.approvals += 1
        self.last_trade_time = time.time()

        approved_signal = ApprovedSignal(
            token_address=address,
            amount_sol=amount,
            approved=True,
            reason="Vsechny kontroly OK",
            original_signal=signal_data,
        )

        # Aktualizujeme peak balance
        if state.balance_sol > self.peak_balance:
            self.peak_balance = state.balance_sol

        logger.info(f"APPROVED: {address} | {amount} SOL")
        db.log_event(
            "RiskGuard", "APPROVED",
            f"APPROVED: {address} | {amount} SOL | score={signal_data.get('score', 0):.1f}",
            level="SUCCESS",
        )
        await bus.emit("RISK_APPROVED_FOR_EXECUTION", approved_signal.__dict__)
        return True

    async def _veto(self, address: str, reason: str, signal_data: Dict[str, Any]):
        self.vetoes += 1
        logger.warning(f"VETO: {address} | {reason}")
        db.log_event(
            "RiskGuard", "VETO",
            f"VETO: {address} | {reason}",
            level="WARNING",
        )
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
