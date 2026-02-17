# soubor: modules/executor.py
# Opravy:
# - B4+L1: entry_price se propaguje z pipeline (original_signal -> price),
#   fallback na DexScreener data z payloadu
# - L4: daily_pnl je portfolio-based (SOL), ne prostý součet %
# - L5: exit trade dělá UPDATE existujícího záznamu místo duplicitního INSERT
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
        logger.info(f"EXECUTOR START – Mod: {state.mode}")

        bus.subscribe("RISK_APPROVED_FOR_EXECUTION", self.execute_trade)
        bus.subscribe("COIN_UPDATE", self._monitor_positions)

        asyncio.create_task(self._heartbeat())

    async def stop(self):
        self.is_running = False
        logger.info("Executor zastaven")

    async def _heartbeat(self):
        while self.is_running:
            await asyncio.sleep(60)
            logger.info(
                f"Executor aktivni | Otevrene pozice: {len(self.active_positions)} | "
                f"Celkem obchodu: {self.trade_count}"
            )

    async def execute_trade(self, approval_data: Dict[str, Any]):
        address = approval_data.get("token_address")
        amount = approval_data.get("amount_sol")

        if address in self.active_positions:
            logger.info(f"Pozice pro {address} jiz existuje, preskakuji.")
            return

        logger.info(f"EXECUTING BUY: {address} | Amount: {amount} SOL")

        # B4+L1: Propagace price skrz pipeline
        # original_signal obsahuje data z Analyzera, který je dostává z Hunter payloadu
        orig = approval_data.get("original_signal", {})
        entry_price = 0.0

        # Zkusíme různé cesty kde se price může nacházet
        if orig.get("price"):
            entry_price = float(orig["price"])
        elif orig.get("detailed_scores", {}).get("price"):
            entry_price = float(orig["detailed_scores"]["price"])

        if entry_price == 0:
            # Fallback pro SHADOW mód - simulovaná cena
            entry_price = 0.000001
            logger.warning(f"Entry price = 0 pro {address}, pouzivam fallback {entry_price}")

        self.active_positions[address] = {
            "entry_price": entry_price,
            "current_price": entry_price,
            "amount_sol": amount,
            "timestamp": time.time(),
            "highest_price": entry_price,
            "status": "OPEN",
            "trade_db_id": None,  # L5: uložíme ID záznamu pro UPDATE
        }

        self.trade_count += 1
        state.open_positions = len(self.active_positions)
        state.balance_sol -= amount
        state.save()

        logger.info(f"POSITION OPENED: {address} at {entry_price}")

        # Uložíme do DB a zapamatujeme si ID
        try:
            conn = db.connect()
            cursor = conn.execute(
                "INSERT INTO trades (mint, entry_price, exit_price, profit_pct, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (address, entry_price, None, 0, "OPEN"),
            )
            conn.commit()
            self.active_positions[address]["trade_db_id"] = cursor.lastrowid
        except Exception as e:
            logger.error(f"Chyba pri ukladani trade do DB: {e}")

    async def _monitor_positions(self, payload: Dict[str, Any]):
        address = payload.get("mint")
        if address not in self.active_positions:
            return

        pos = self.active_positions[address]
        current_price = payload.get("price", 0)
        if current_price == 0:
            return

        pos["current_price"] = current_price
        if current_price > pos["highest_price"]:
            pos["highest_price"] = current_price

        entry = pos["entry_price"]
        if entry == 0:
            return

        profit_pct = ((current_price - entry) / entry) * 100

        # 1. Stop Loss (-15%)
        if profit_pct <= -15.0:
            await self._exit_position(address, current_price, "STOP_LOSS")

        # 2. Take Profit (+100%)
        elif profit_pct >= 100.0:
            await self._exit_position(address, current_price, "TAKE_PROFIT_100")

        # 3. Trailing Stop (pokud profit > 20% a cena spadla o 20% od maxima)
        elif profit_pct > 20.0:
            drop_from_high = (
                (pos["highest_price"] - current_price) / pos["highest_price"]
            ) * 100
            if drop_from_high >= 20.0:
                await self._exit_position(address, current_price, "TRAILING_STOP")

    async def _exit_position(self, address: str, exit_price: float, reason: str):
        if address not in self.active_positions:
            return

        pos = self.active_positions.pop(address)
        entry = pos["entry_price"]
        profit_pct = ((exit_price - entry) / entry) * 100 if entry > 0 else 0
        # L4: profit v SOL (portfolio-based), ne jen % součet
        profit_sol = pos["amount_sol"] * (profit_pct / 100)

        logger.info(
            f"EXIT POSITION: {address} | Reason: {reason} | "
            f"Profit: {profit_pct:.2f}% ({profit_sol:.4f} SOL)"
        )

        # Aktualizace stavu
        state.open_positions = len(self.active_positions)
        state.balance_sol += pos["amount_sol"] + profit_sol
        # L4: daily_pnl v SOL (ne prostý součet %)
        state.daily_pnl += profit_sol
        state.save()

        # L5: UPDATE existujícího záznamu místo nového INSERT
        trade_id = pos.get("trade_db_id")
        try:
            conn = db.connect()
            if trade_id:
                conn.execute(
                    "UPDATE trades SET exit_price=?, profit_pct=?, status=? WHERE id=?",
                    (exit_price, profit_pct, f"CLOSED_{reason}", trade_id),
                )
            else:
                # Fallback: pokud nemáme ID, uložíme nový záznam
                conn.execute(
                    "INSERT INTO trades (mint, entry_price, exit_price, profit_pct, status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (address, entry, exit_price, profit_pct, f"CLOSED_{reason}"),
                )
            conn.commit()
        except Exception as e:
            logger.error(f"Chyba pri ukladani exit trade do DB: {e}")

        await bus.emit(
            "POSITION_CLOSED",
            {
                "token_address": address,
                "profit_pct": profit_pct,
                "profit_sol": profit_sol,
                "reason": reason,
            },
        )


executor = Executor()
