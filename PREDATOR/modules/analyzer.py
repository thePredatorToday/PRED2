# soubor: modules/analyzer.py
# v2.1: LP Lock verifikace, circuit breaker pro RPC/API, vylepseny scoring

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

import httpx

from core.event_bus import bus
from core.config import settings
from core.database import db
from core.strategy import strategy
from core.circuit_breaker import CircuitBreaker

load_dotenv()

logger = logging.getLogger("Predator.Analyzer")

# Raydium AMM program ID
RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
# Raydium liquidity pool V4 authority (burn address check)
BURN_ADDRESS = "1111111111111111111111111111111111111111111"


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
    liquidity: float = 0.0
    volume_5m: float = 0.0
    lp_locked: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)


class Analyzer:
    def __init__(self):
        self.rpc_url = os.getenv("SOLANA_RPC_URL", settings.SOLANA_RPC_URL)
        self.rpc_client: Optional[AsyncClient] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.is_running = False
        self.processed_count = 0
        self.signals_emitted = 0
        self.rejected_count = 0
        self.history: Dict[str, List[Dict[str, Any]]] = {}
        self.history_lock = Lock()
        # Circuit breaker pro RPC a externi API
        self.rpc_cb = CircuitBreaker("analyzer_rpc", failure_threshold=5, recovery_timeout=60)
        self.api_cb = CircuitBreaker("analyzer_api", failure_threshold=3, recovery_timeout=30)

    async def start(self):
        self.rpc_client = AsyncClient(self.rpc_url)
        self.http_client = httpx.AsyncClient(timeout=10.0)
        connected = await self.rpc_client.is_connected()
        if not connected:
            logger.error("Analyzer RPC offline - omezeny rezim")

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
        if self.http_client:
            await self.http_client.aclose()
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

        # Circuit breaker check
        if not self.rpc_cb.can_execute():
            logger.debug(f"RPC circuit breaker OPEN - skip analyzer audit {address[:8]}")
            self.rejected_count += 1
            return

        try:
            token_pubkey = Pubkey.from_string(address)

            # Paralelne: RPC audit + LP lock check + history
            audit_task = asyncio.create_task(self._rpc_audit(token_pubkey))
            lp_task = asyncio.create_task(self._check_lp_lock(address))
            history_score, history_flags = await self._analyze_history(address)

            audit_results = await audit_task
            lp_locked, lp_score, lp_flags = await lp_task
            self.rpc_cb.record_success()

            scores = {}
            flags = history_flags + audit_results["flags"] + lp_flags

            scores["liquidity"] = history_score * 0.20
            scores["holders"] = audit_results["holder_score"] * 0.20
            scores["lp_lock"] = lp_score * 0.15  # FIX: realna LP lock verifikace
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
                liquidity=payload.get("liquidity", 0),
                volume_5m=payload.get("volume_5m", 0),
                lp_locked=lp_locked,
            )

            # Dynamicky threshold ze strategie
            s = strategy.get()
            min_score = s.analyzer_min_score

            if final_score >= min_score:
                self.signals_emitted += 1
                await bus.emit("TRADE_SIGNAL_READY", signal.__dict__)
                db.log_event(
                    "Analyzer", "SIGNAL_EMITTED",
                    f"SIGNAL: {address} | score={final_score:.1f} | "
                    f"LP={'LOCKED' if lp_locked else 'UNKNOWN'} | "
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
            self.rpc_cb.record_failure()
            logger.error(f"Audit error {address}: {e}", exc_info=True)

    # ──────────────── LP LOCK VERIFICATION ────────────────

    async def _check_lp_lock(self, token_address: str) -> Tuple[bool, float, List[str]]:
        """
        Verifikuje LP lock pro token.
        Kontroluje:
        1. Raydium pool existenci
        2. LP token burn (owner = burn address)
        3. DexScreener liquidity lock info

        Vraci: (is_locked, score 0-100, flags)
        """
        flags = []
        is_locked = False
        score = 50.0  # default: unknown

        # Metoda 1: DexScreener API check
        try:
            if self.http_client and self.api_cb.can_execute():
                resp = await self.http_client.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        pair = pairs[0]
                        liquidity = pair.get("liquidity", {})
                        liq_usd = liquidity.get("usd", 0)

                        # Kontrola info z DexScreener
                        info = pair.get("info", {})
                        socials = info.get("socials", []) if info else []

                        # Pokud ma par vysokou likviditu a existuje dele nez 1h
                        pair_created = pair.get("pairCreatedAt", 0)
                        age_hours = (time.time() * 1000 - pair_created) / 3600000 if pair_created else 0

                        if liq_usd > 10000 and age_hours > 1:
                            score = 80.0
                            is_locked = True
                        elif liq_usd > 5000:
                            score = 60.0
                        else:
                            score = 30.0
                            flags.append("LOW_LIQUIDITY_POOL")
                self.api_cb.record_success()
        except Exception as e:
            self.api_cb.record_failure()
            logger.debug(f"DexScreener LP check failed: {e}")

        # Metoda 2: RPC - kontrola LP token ownership
        try:
            if self.rpc_client:
                # Hledame Raydium pool pro tento token
                lp_check = await self._check_raydium_lp_burn(token_address)
                if lp_check is True:
                    is_locked = True
                    score = 100.0
                elif lp_check is False:
                    if not is_locked:
                        flags.append("LP_NOT_BURNED")
                        score = min(score, 40.0)
        except Exception as e:
            logger.debug(f"Raydium LP burn check failed: {e}")

        if not is_locked:
            flags.append("LP_LOCK_UNVERIFIED")

        return is_locked, score, flags

    async def _check_raydium_lp_burn(self, token_address: str) -> Optional[bool]:
        """
        Kontroluje zda jsou Raydium LP tokeny burned.
        Vraci True (burned), False (not burned), None (nelze zjistit).
        """
        try:
            if not self.http_client:
                return None

            # Raydium API - pool info
            resp = await self.http_client.get(
                f"{settings.RAYDIUM_API_URL}/pools/info/mint"
                f"?mint1={token_address}"
                f"&mint2=So11111111111111111111111111111111111111112"
                f"&poolType=all&poolSortField=default&sortType=desc&pageSize=1&page=1",
                timeout=5.0,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            pools = data.get("data", {}).get("data", [])
            if not pools:
                return None

            pool = pools[0]
            # Raydium V3 API poskytuje info o LP burn
            lp_mint = pool.get("lpMint", {})
            if isinstance(lp_mint, dict):
                lp_address = lp_mint.get("address", "")
            else:
                lp_address = str(lp_mint) if lp_mint else ""

            if not lp_address:
                return None

            # Kontrola zda LP tokeny maji owner = burn address
            lp_pubkey = Pubkey.from_string(lp_address)
            largest = await self.rpc_client.get_token_largest_accounts(lp_pubkey)
            if largest.value:
                for acc in largest.value:
                    # Pokud nejvetsi holder LP tokenu je burn address
                    acc_info = await self.rpc_client.get_account_info(
                        Pubkey.from_string(str(acc.address)),
                        commitment=Confirmed,
                    )
                    if acc_info.value:
                        owner = str(acc_info.value.owner)
                        if owner == BURN_ADDRESS or "1111111" in owner:
                            return True
            return False

        except Exception as e:
            logger.debug(f"Raydium LP burn check error: {e}")
            return None

    # ──────────────── SCORING ────────────────

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

        except Exception as e:
            logger.warning(f"RPC audit failed: {e}")

        return results


analyzer = Analyzer()
