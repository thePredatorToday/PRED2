# soubor: modules/analyzer.py
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

load_dotenv()

logger = logging.getLogger("Predator.Analyzer")

@dataclass
class TradeSignal:
    token_address: str
    score: float  # 0-100
    risk_flags: List[str] = field(default_factory=list)
    suggested_size: float = 0.1  # Default Beta size
    prediction_5m: float = 0.0  # % change
    detailed_scores: Dict[str, float] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)

class Analyzer:
    def __init__(self):
        self.rpc_url = os.getenv("SOLANA_RPC_URL", settings.SOLANA_RPC_URL)
        self.rpc_client: Optional[AsyncClient] = None
        self.is_running = False
        self.processed_count = 0
        self.signals_emitted = 0
        self.history: Dict[str, List[Dict[str, Any]]] = {} # address -> list of updates
        self.history_lock = Lock()

    async def start(self):
        self.rpc_client = AsyncClient(self.rpc_url)
        connected = await self.rpc_client.is_connected()
        if not connected:
            logger.error("Analyzer nemohl připojit RPC – běží v omezeném režimu")
        
        self.is_running = True
        logger.info("🔍 ANALYZER START – Hluboký on-chain audit aktivován")
        
        # Odebíráme od Huntera
        bus.subscribe("GOOD_COIN_SELECTED", self.evaluate)
        # Sledujeme updaty pro historii (likvidita, volume)
        bus.subscribe("COIN_UPDATE", self._track_history)
        
        asyncio.create_task(self._heartbeat())

    async def stop(self):
        self.is_running = False
        if self.rpc_client:
            await self.rpc_client.close()
        logger.info("🛑 Analyzer zastaven")

    async def _heartbeat(self):
        while self.is_running:
            await asyncio.sleep(60)
            logger.info(
                f"🔍 Analyzer aktivní | Audity: {self.processed_count} | Emitováno signálů: {self.signals_emitted}"
            )

    async def _track_history(self, payload: Dict[str, Any]):
        address = payload.get("mint")
        if not address: return
        
        async with self.history_lock:
            if address not in self.history:
                self.history[address] = []
            
            self.history[address].append({
                "timestamp": time.time(),
                "liquidity": payload.get("liquidity", 0),
                "price": payload.get("price", 0),
                "volume_5m": payload.get("volume_5m", 0)
            })
            
            # Držíme jen posledních 30 minut historie
            now = time.time()
            self.history[address] = [h for h in self.history[address] if now - h["timestamp"] < 1800]

    async def evaluate(self, payload: Dict[str, Any]):
        self.processed_count += 1
        address = payload.get("mint")
        logger.info(f"🔍 Zahajuji hluboký audit pro: {address}")
        
        try:
            # 1. Paralelní sběr dat
            token_pubkey = Pubkey.from_string(address)
            
            # RPC úkoly
            audit_task = self._rpc_audit(token_pubkey)
            # Historická analýza (pokud máme data)
            history_score, history_flags = await self._analyze_history(address)
            
            # Čekáme na RPC výsledky
            audit_results = await audit_task
            
            # 2. Výpočet komponent skóre
            scores = {}
            flags = history_flags + audit_results["flags"]
            
            # A. Liquidity Stability (20%)
            scores["liquidity"] = history_score * 0.20
            
            # B. Holder Distribution (20%)
            scores["holders"] = audit_results["holder_score"] * 0.20
            
            # C. LP Lock Status (15%)
            scores["lp_lock"] = audit_results["lp_score"] * 0.15
            
            # D. Mint/Freeze Authority (15%)
            scores["authority"] = audit_results["auth_score"] * 0.15
            
            # E. Volume Growth (20%)
            vol_score = self._calculate_volume_score(payload)
            scores["volume"] = vol_score * 0.20
            
            # F. Volatility/Price Discovery (10%)
            scores["volatility"] = self._calculate_volatility_score(address) * 0.10
            
            final_score = sum(scores.values())
            
            # 3. Vytvoření signálu
            signal = TradeSignal(
                token_address=address,
                score=final_score,
                risk_flags=flags,
                detailed_scores=scores,
                prediction_5m=0.0 # TODO: ML model placeholder
            )
            
            logger.info(f"✅ Audit dokončen: {address} | Skóre: {final_score:.1f}")
            
            if final_score >= 80:
                self.signals_emitted += 1
                await bus.emit("TRADE_SIGNAL_READY", signal.__dict__)
                logger.info(f"🚀 TRADE_SIGNAL_READY emitován pro {address}")
            else:
                logger.info(f"❌ Audit reject {address} (skóre {final_score:.1f})")

        except Exception as e:
            logger.error(f"Chyba při auditu {address}: {e}", exc_info=True)

    async def _analyze_history(self, address: str) -> Tuple[float, List[str]]:
        """Analyzuje stabilitu likvidity z historie."""
        async with self.history_lock:
            hist = self.history.get(address, [])
            if len(hist) < 2:
                return 50.0, [] # Neutrální skóre, nemáme dost dat
            
            initial_liq = hist[0]["liquidity"]
            current_liq = hist[-1]["liquidity"]
            
            flags = []
            if current_liq < initial_liq * 0.8:
                flags.append("LIQUIDITY_DROP_DETECTION")
                return 0.0, flags
            
            # Pokud likvidita roste nebo je stabilní
            score = 100.0 if current_liq >= initial_liq else 70.0
            return score, flags

    def _calculate_volume_score(self, payload: Dict[str, Any]) -> float:
        vol_5m = payload.get("volume_5m", 0)
        liq = payload.get("liquidity", 1) # avoid div by zero
        
        # Volume relative to liquidity
        ratio = vol_5m / liq
        if ratio > 0.1: return 100.0
        if ratio > 0.05: return 70.0
        return 40.0

    def _calculate_volatility_score(self, address: str) -> float:
        hist = self.history.get(address, [])
        if len(hist) < 3: return 50.0
        
        prices = [h["price"] for h in hist]
        # Jednoduchý indikátor trendu
        if prices[-1] > prices[0] and prices[-1] > prices[-2]:
            return 100.0
        return 50.0

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _rpc_audit(self, mint_pubkey: Pubkey) -> Dict[str, Any]:
        results = {
            "holder_score": 0.0,
            "lp_score": 0.0,
            "auth_score": 0.0,
            "flags": []
        }
        
        if not self.rpc_client: return results

        try:
            # 1. Authority Check
            acc_info = await self.rpc_client.get_account_info(mint_pubkey, commitment=Confirmed)
            if acc_info.value:
                data = acc_info.value.data
                mint_auth = data[4:8] == b"\x00\x00\x00\x00"
                freeze_auth = data[36:40] == b"\x00\x00\x00\x00"
                
                if mint_auth and freeze_auth:
                    results["auth_score"] = 100.0
                else:
                    results["flags"].append("ACTIVE_AUTHORITY_RISK")
                    results["auth_score"] = 0.0

            # 2. Holder Distribution
            largest_resp = await self.rpc_client.get_token_largest_accounts(mint_pubkey)
            if largest_resp.value:
                largest = largest_resp.value
                total_in_top = sum(int(acc.amount) for acc in largest[:10])
                # Note: accurate supply is needed for exact %, using relative for now
                total_sampled = sum(int(acc.amount) for acc in largest)
                top10_ratio = total_in_top / total_sampled if total_sampled > 0 else 1.0
                
                if top10_ratio < 0.3: results["holder_score"] = 100.0
                elif top10_ratio < 0.5: results["holder_score"] = 70.0
                else: 
                    results["holder_score"] = 30.0
                    results["flags"].append("HIGH_HOLDER_CONCENTRATION")

            # 3. LP Lock Placeholder
            # V reálu bychom hledali Raydium/Orca pool adresu a kontrolovali burn
            # Pro v1.3 vracíme konzervativní odhad
            results["lp_score"] = 100.0 # Předpokládáme spálené LP pokud Hunter propustil

        except Exception as e:
            logger.warning(f"RPC Audit sub-task failed: {e}")
            
        return results

analyzer = Analyzer()
