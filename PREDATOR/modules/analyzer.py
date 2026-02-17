# soubor: modules/analyzer.py
# + Dynamická strategie (analyzer_min_score ze strategie)
# + Event logging do DB pro dashboard

import asyncio
from asyncio import Lock
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey
from tenacity import retry, stop_after_attempt, wait_exponential

from core.event_bus import bus
from core.config import settings
from core.database import db
from core.strategy import strategy

load_dotenv()

logger = logging.getLogger("Predator.Analyzer")


@dataclass
class TradeSignal:
    token_address: str
    score: float
    risk_flags: List[str] = field(default_factory=list)
    suggested_size: float = 0.1
    prediction_5m: float = 0.0
    detailed_scores: Dict[str, float] = field(default_factory=dict)
    price: float = 0.0
    market_cap: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class Analyzer:
    def __init__(self):
        self.rpc_url = os.getenv("SOLANA_RPC_URL", settings.SOLANA_RPC_URL)
        self.rpc_client: Optional[AsyncClient] = None
        self.is_running = False
        self.processed_count = 0
        self.signals_emitted = 0
        self.rejected_count = 0
        self.history: Dict[str, List[Dict[str, Any]]] = {}
        self.history_lock = Lock()

    async def start(self):
        self.rpc_client = AsyncClient(self.rpc_url)
        connected = await self.rpc_client.is_connected()
        if not connected:
            logger.error("Analyzer RPC offline – omezeny rezim")

        self.is_running = True
        logger.info("ANALYZER START")

        bus.subscribe("GOOD_COIN_SELECTED", self.evaluate)
        bus.subscribe("COIN_UPDATE", self._track_history)
        bus.subscribe("STRATEGY_CHANGE", self._on_strategy_change)

        asyncio.create_task(self._heartbeat())
        db.log_event("Analyzer", "MODULE_START", "Analyzer spusten")

    async def stop(self):
        self.is_running = False
        if self.rpc_client:
            await self.rpc_client.close()
        logger.info("Analyzer zastaven")

    async def _heartbeat(self):
        while self.is_running:
            await asyncio.sleep(60)
            s = strategy.get()
            logger.info(
                f"Analyzer | Audity: {self.processed_count} | "
                f"Signals: {self.signals_emitted} | Reject: {self.rejected_count} | "
                f"Min score: {s.analyzer_min_score}"
            )

    async def _on_strategy_change(self, payload: Dict[str, Any]):
        name = payload.get("strategy", "default")
        strategy.set_strategy(name)
        s = strategy.get()
        db.log_event(
            "Analyzer", "STRATEGY_CHANGE",
            f"Strategie: {s.name} | min_score={s.analyzer_min_score}",
        )

    async def _track_history(self, payload: Dict[str, Any]):
        address = payload.get("mint")
        if not address:
            return

        async with self.history_lock:
            if address not in self.history:
                self.history[address] = []

            self.history[address].append({
                "timestamp": time.time(),
                "liquidity": payload.get("liquidity", 0),
                "price": payload.get("price", 0),
                "volume_5m": payload.get("volume_5m", 0),
            })

            now = time.time()
            self.history[address] = [
                h for h in self.history[address] if now - h["timestamp"] < 1800
            ]

    async def evaluate(self, payload: Dict[str, Any]):
        self.processed_count += 1
        address = payload.get("mint")
        logger.info(f"Audit: {address}")

        try:
            token_pubkey = Pubkey.from_string(address)

            audit_task = self._rpc_audit(token_pubkey)
            history_score, history_flags = await self._analyze_history(address)
            audit_results = await audit_task

            scores = {}
            flags = history_flags + audit_results["flags"]

            scores["liquidity"] = history_score * 0.20
            scores["holders"] = audit_results["holder_score"] * 0.20
            scores["lp_lock"] = audit_results["lp_score"] * 0.15
            scores["authority"] = audit_results["auth_score"] * 0.15
            scores["volume"] = self._calculate_volume_score(payload) * 0.20
            scores["volatility"] = self._calculate_volatility_score(address) * 0.10

            # Propagace price a market_cap
            scores["price"] = payload.get("price", 0)

            final_score = sum(v for k, v in scores.items() if k != "price")

            signal = TradeSignal(
                token_address=address,
                score=final_score,
                risk_flags=flags,
                detailed_scores=scores,
                price=payload.get("price", 0),
                market_cap=payload.get("market_cap", 0),
            )

            # Dynamický threshold ze strategie
            s = strategy.get()
            min_score = s.analyzer_min_score

            if final_score >= min_score:
                self.signals_emitted += 1
                await bus.emit("TRADE_SIGNAL_READY", signal.__dict__)
                db.log_event(
                    "Analyzer", "SIGNAL_EMITTED",
                    f"SIGNAL: {address} | score={final_score:.1f} | "
                    f"flags={','.join(flags) if flags else 'none'}",
                    level="SUCCESS",
                )
            else:
                self.rejected_count += 1
                db.log_event(
                    "Analyzer", "AUDIT_REJECT",
                    f"REJECT: {address} | score={final_score:.1f} < {min_score} | "
                    f"flags={','.join(flags) if flags else 'none'}",
                )

        except Exception as e:
            logger.error(f"Audit error {address}: {e}", exc_info=True)

    async def _analyze_history(self, address: str) -> Tuple[float, List[str]]:
        async with self.history_lock:
            hist = self.history.get(address, [])
            if len(hist) < 2:
                return 50.0, []

            initial_liq = hist[0]["liquidity"]
            current_liq = hist[-1]["liquidity"]

            flags = []
            if current_liq < initial_liq * 0.8:
                flags.append("LIQUIDITY_DROP")
                return 0.0, flags

            score = 100.0 if current_liq >= initial_liq else 70.0
            return score, flags

    def _calculate_volume_score(self, payload: Dict[str, Any]) -> float:
        vol_5m = payload.get("volume_5m", 0)
        liq = payload.get("liquidity", 1)
        ratio = vol_5m / liq
        if ratio > 0.1:
            return 100.0
        if ratio > 0.05:
            return 70.0
        return 40.0

    def _calculate_volatility_score(self, address: str) -> float:
        hist = self.history.get(address, [])
        if len(hist) < 3:
            return 50.0
        prices = [h["price"] for h in hist]
        if prices[-1] > prices[0] and prices[-1] > prices[-2]:
            return 100.0
        return 50.0

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _rpc_audit(self, mint_pubkey: Pubkey) -> Dict[str, Any]:
        results = {
            "holder_score": 0.0,
            "lp_score": 0.0,
            "auth_score": 0.0,
            "flags": [],
        }

        if not self.rpc_client:
            return results

        try:
            acc_info = await self.rpc_client.get_account_info(mint_pubkey, commitment=Confirmed)
            if acc_info.value:
                data = acc_info.value.data
                mint_auth = data[4:8] == b"\x00\x00\x00\x00"
                freeze_auth = data[36:40] == b"\x00\x00\x00\x00"

                if mint_auth and freeze_auth:
                    results["auth_score"] = 100.0
                else:
                    results["flags"].append("ACTIVE_AUTHORITY")
                    results["auth_score"] = 0.0

            largest_resp = await self.rpc_client.get_token_largest_accounts(mint_pubkey)
            if largest_resp.value:
                largest = largest_resp.value
                total_in_top = sum(int(acc.amount) for acc in largest[:10])
                total_sampled = sum(int(acc.amount) for acc in largest)
                top10_ratio = total_in_top / total_sampled if total_sampled > 0 else 1.0

                if top10_ratio < 0.3:
                    results["holder_score"] = 100.0
                elif top10_ratio < 0.5:
                    results["holder_score"] = 70.0
                else:
                    results["holder_score"] = 30.0
                    results["flags"].append("HIGH_CONCENTRATION")

            results["lp_score"] = 100.0

        except Exception as e:
            logger.warning(f"RPC audit failed: {e}")

        return results


analyzer = Analyzer()
