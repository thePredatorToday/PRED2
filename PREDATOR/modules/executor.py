# soubor: modules/executor.py
import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.event_bus import bus
from core.system_state import state
from core.database import db
from core.config import settings

logger = logging.getLogger("Predator.Executor")

class Executor:
    def __init__(self):
        self.is_running = False
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self.trade_count = 0

    async def start(self):
        self.is_running = True
        logger.info(f"⚡ EXECUTOR START – Mód: {state.mode}")
        
        # Odebíráme schválené signály od RiskGuard
        bus.subscribe("RISK_APPROVED_FOR_EXECUTION", self.execute_trade)
        # Sledujeme updaty cen pro trailing stop / exit
        bus.subscribe("COIN_UPDATE", self._monitor_positions)
        
        asyncio.create_task(self._heartbeat())

    async def stop(self):
        self.is_running = False
        logger.info("🛑 Executor zastaven")

    async def _heartbeat(self):
        while self.is_running:
            await asyncio.sleep(60)
            logger.info(
                f"⚡ Executor aktivní | Otevřené pozice: {len(self.active_positions)} | "
                f"Celkem obchodů: {self.trade_count}"
            )

    async def execute_trade(self, approval_data: Dict[str, Any]):
        address = approval_data.get("token_address")
        amount = approval_data.get("amount_sol")
        
        if address in self.active_positions:
            logger.info(f"⚡ Pozice pro {address} již existuje, přeskakuji.")
            return

        logger.info(f"⚡ EXECUTING BUY: {address} | Amount: {amount} SOL")
        
        # Simulace nákupu (Shadow/Paper mode)
        # V Live by zde byla volána Jupiter API
        entry_price = approval_data.get("original_signal", {}).get("detailed_scores", {}).get("price", 0)
        if entry_price == 0:
            # Zkusíme vytáhnout z historie signálu
            entry_price = 1.0 # Fallback pro simulaci
            
        self.active_positions[address] = {
            "entry_price": entry_price,
            "current_price": entry_price,
            "amount_sol": amount,
            "timestamp": time.time(),
            "highest_price": entry_price,
            "status": "OPEN"
        }
        
        self.trade_count += 1
        state.open_positions = len(self.active_positions)
        state.balance_sol -= amount # Odečteme z virtuálního balancu
        state.save()
        
        logger.info(f"✅ POSITION OPENED: {address} at {entry_price}")
        
        # Uložíme do DB
        db.save_trade({
            "mint": address,
            "entry_price": entry_price,
            "exit_price": None,
            "profit_pct": 0,
            "status": "OPEN"
        })

    async def _monitor_positions(self, payload: Dict[str, Any]):
        address = payload.get("mint")
        if address not in self.active_positions:
            return
            
        pos = self.active_positions[address]
        current_price = payload.get("price", 0)
        if current_price == 0: return
        
        pos["current_price"] = current_price
        if current_price > pos["highest_price"]:
            pos["highest_price"] = current_price
            
        # Logika exitu (jednoduchý Trailing Stop / Take Profit)
        # Spec: Sell 80% at 2x, hold 20% moonbag (zjednodušeno pro v1.3)
        
        profit_pct = ((current_price - pos["entry_price"]) / pos["entry_price"]) * 100
        
        # 1. Stop Loss (-15%)
        if profit_pct <= -15.0:
            await self._exit_position(address, current_price, "STOP_LOSS")
            
        # 2. Take Profit (Moon Bag logic - zjednodušeno: exit vše při +100%)
        elif profit_pct >= 100.0:
            await self._exit_position(address, current_price, "TAKE_PROFIT_100")
            
        # 3. Trailing Stop (pokud cena klesne o 20% od maxima poté co jsme v profitu > 20%)
        elif profit_pct > 20.0:
             drop_from_high = ((pos["highest_price"] - current_price) / pos["highest_price"]) * 100
             if drop_from_high >= 20.0:
                 await self._exit_position(address, current_price, "TRAILING_STOP")

    async def _exit_position(self, address: str, exit_price: float, reason: str):
        if address not in self.active_positions: return
        
        pos = self.active_positions.pop(address)
        profit_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
        profit_sol = pos["amount_sol"] * (profit_pct / 100)
        
        logger.info(f"⚡ EXIT POSITION: {address} | Reason: {reason} | Profit: {profit_pct:.2f}% ({profit_sol:.4f} SOL)")
        
        # Aktualizace stavu
        state.open_positions = len(self.active_positions)
        state.balance_sol += (pos["amount_sol"] + profit_sol)
        state.daily_pnl += profit_pct # Velmi zjednodušený PnL
        state.save()
        
        # Uložíme do DB (v reálu by to byl UPDATE, ale save_trade je teď INSERT)
        db.save_trade({
            "mint": address,
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "profit_pct": profit_pct,
            "status": f"CLOSED_{reason}"
        })
        
        await bus.emit("POSITION_CLOSED", {
            "token_address": address,
            "profit_pct": profit_pct,
            "reason": reason
        })

executor = Executor()
