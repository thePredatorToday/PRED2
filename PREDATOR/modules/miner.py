# soubor: modules/miner.py
import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
import websockets
from pydantic import BaseModel, Field, ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from core.database import db
from core.event_bus import bus

logger = logging.getLogger("Predator.Miner")

PUMP_WS_URI = "wss://pumpportal.fun/api/data"
HIGH_VOLUME_5M_THRESHOLD = 1000
DEAD_LIQUIDITY_THRESHOLD = 100


class MinerPayload(BaseModel):
    mint: str = Field(..., description="Token mint address")
    name: str = Field(default="Unknown", description="Token name")
    symbol: str = Field(default="?", description="Token symbol")
    price: float = Field(default=0.0, ge=0)
    market_cap: float = Field(default=0.0, ge=0)
    liquidity: float = Field(default=0.0, ge=0)
    volume_24h: float = Field(default=0.0, ge=0)
    volume_5m: float = Field(default=0.0, ge=0)


class Miner:
    def __init__(self):
        self.is_running = False
        self.priority_queue: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"priority": "LOW", "last_scan": 0.0, "data": None}
        )
        # M1: Dedup s TTL (dict hash -> timestamp) místo neomezeného setu
        self.dedup_set: Dict[str, float] = {}
        self.dedup_ttl = 3600  # 1 hodina TTL
        self.rate_limit_backoff = 5
        self.http_client = httpx.AsyncClient(timeout=10.0)
        self.dex_semaphore = asyncio.Semaphore(5)
        self.mint_counter_last_heartbeat = 0
        self.emit_counter_last_heartbeat = 0
        # B1: Atributy pro _rotate_rpc (chyběly → AttributeError)
        self.rpc_endpoints = [
            "https://api.mainnet-beta.solana.com",
        ]
        self.current_rpc_idx = 0

    async def start(self):
        self.is_running = True
        logger.info("🚀 MINER START – Pump.fun WS + DexScreener fallback aktivován")
        logger.info(
            f"Scan intervaly: HIGH 5s | LOW 300s | DEAD purge < ${DEAD_LIQUIDITY_THRESHOLD}"
        )
        asyncio.create_task(self.pump_fun_ws_listener())
        asyncio.create_task(self.scan_loop())
        asyncio.create_task(self._heartbeat())
        await asyncio.Event().wait()

    async def stop(self):
        self.is_running = False
        await self.http_client.aclose()
        logger.info("🛑 Miner zastaven")

    async def _heartbeat(self):
        while self.is_running:
            await asyncio.sleep(60)
            new_mints = self.mint_counter_last_heartbeat
            emitted = self.emit_counter_last_heartbeat
            q_size = len(self.priority_queue)
            dedup_size = len(self.dedup_set)
            logger.info(
                f"Miner aktivni | Nove minty/min: {new_mints} | "
                f"Emitovano: {emitted} | Queue: {q_size} | Dedup: {dedup_size}"
            )
            # M4: Reset countery PRED logem (uz je spravne)
            self.mint_counter_last_heartbeat = 0
            self.emit_counter_last_heartbeat = 0
            # M1: Periodicky cistit dedup set
            self._cleanup_dedup()

    async def pump_fun_ws_listener(self):
        while self.is_running:
            try:
                async with websockets.connect(PUMP_WS_URI) as ws:
                    logger.info("Připojeno k Pump.fun realtime WS")
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    logger.info("Odeslána subscription na nové tokeny")

                    while self.is_running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=120.0)
                            try:
                                data = json.loads(msg)
                            except json.JSONDecodeError:
                                continue

                            if "mint" not in data:
                                continue

                            mint = data["mint"]
                            if self._is_dupe(mint):
                                continue

                            # M1: TTL-based dedup
                            h = hashlib.sha256(mint.encode()).hexdigest()
                            self.dedup_set[h] = time.time()
                            self.mint_counter_last_heartbeat += 1

                            logger.debug(
                                f"Nový mint: {mint} | {data.get('name', 'N/A')}"
                            )

                            payload_dict = {
                                "mint": mint,
                                "name": data.get("name", "Unknown"),
                                "symbol": data.get("symbol", "?"),
                                "price": float(data.get("priceUsd", 0)),
                                "market_cap": float(data.get("fdv", 0)),
                                "liquidity": float(
                                    data.get("liquidity", {}).get("usd", 0)
                                ),
                                "volume_24h": float(
                                    data.get("volume", {}).get("h24", 0)
                                ),
                                "volume_5m": float(data.get("volume", {}).get("m5", 0)),
                            }

                            try:
                                validated = MinerPayload(**payload_dict)
                                await bus.emit("NEW_COIN_FOUND", validated.dict())
                                self.emit_counter_last_heartbeat += 1
                                logger.debug(f"Emitováno NEW_COIN_FOUND pro {mint}")
                            except ValidationError as e:
                                logger.debug(f"Nevalidní WS data: {e}")

                            volume_5m = payload_dict.get("volume_5m", 0)
                            liquidity = payload_dict.get("liquidity", 0)

                            prio = (
                                "HIGH"
                                if liquidity > 2000
                                or volume_5m > HIGH_VOLUME_5M_THRESHOLD
                                else "LOW"
                            )
                            self.priority_queue[mint] = {
                                "priority": prio,
                                "last_scan": time.time(),
                                "data": payload_dict,
                            }

                        except asyncio.TimeoutError:
                            logger.warning("WS timeout – pinguji")
                            await ws.ping()
                        except websockets.ConnectionClosed as e:
                            logger.warning(f"WS uzavřeno: {e} – reconnectuji")
                            raise
            except Exception as e:
                logger.error(f"Miner WS error: {e}", exc_info=True)
                await asyncio.sleep(self.rate_limit_backoff)
                self.rate_limit_backoff = min(30, self.rate_limit_backoff * 2)

    async def scan_loop(self):
        logger.info("Scan loop spuštěn")
        while self.is_running:
            now = time.time()
            to_purge = []
            for mint, info in list(self.priority_queue.items()):
                if info["priority"] == "PURGE":
                    to_purge.append(mint)
                    continue
                interval = 5 if info["priority"] == "HIGH" else 300
                if now - info["last_scan"] > interval:
                    try:
                        updated = await self._update_from_dexscreener(mint)
                        if updated:
                            info["data"].update(updated)
                            info["last_scan"] = now
                            if updated.get("volume_5m", 0) > HIGH_VOLUME_5M_THRESHOLD:
                                info["priority"] = "HIGH"
                            if updated.get("liquidity", 0) < DEAD_LIQUIDITY_THRESHOLD:
                                info["priority"] = "PURGE"
                                logger.info(f"PURGE mrtvý pool: {mint}")
                            await bus.emit("COIN_UPDATE", info["data"])
                            try:
                                db.save_coin_update(info["data"])
                            except Exception as e:
                                logger.debug(f"Failed saving coin update to DB: {e}")
                    except Exception as e:
                        logger.error(f"Update {mint} selhal: {e}")
                        self._rotate_rpc()
            for mint in to_purge:
                del self.priority_queue[mint]
            await asyncio.sleep(1)

    async def _enrich_payload(self, mint: str, ws_data: Dict) -> Optional[Dict]:
        base = {
            "mint": mint,
            "name": ws_data.get("name", "Unknown"),
            "symbol": ws_data.get("symbol", "?"),
            "timestamp": ws_data.get("timestamp", time.time()),
        }
        async with self.dex_semaphore:
            try:
                resp = await self.http_client.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                )
                if resp.status_code == 200:
                    pairs = resp.json().get("pairs", [])
                    if pairs:
                        p = pairs[0]
                        base.update(
                            {
                                "price": float(p.get("priceUsd", 0)),
                                "market_cap": float(p.get("fdv", 0)),
                                "liquidity": float(
                                    p.get("liquidity", {}).get("usd", 0)
                                ),
                                "volume_24h": float(p.get("volume", {}).get("h24", 0)),
                                "volume_5m": float(p.get("volume", {}).get("m5", 0)),
                            }
                        )
            except Exception as e:
                logger.debug(f"DexScreener selhal {mint}: {e}")
        return base if base.get("liquidity", 0) > 0 else None

    async def _update_from_dexscreener(self, mint: str) -> Dict:
        async with self.dex_semaphore:
            try:
                resp = await self.http_client.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                )
                if resp.status_code == 429:
                    self.rate_limit_backoff = min(60, self.rate_limit_backoff * 2)
                    await asyncio.sleep(self.rate_limit_backoff)
                    return {}
                if resp.status_code != 200:
                    return {}
                pairs = resp.json().get("pairs", [])
                if not pairs:
                    return {}
                p = pairs[0]
                return {
                    "price": float(p.get("priceUsd", 0)),
                    "market_cap": float(p.get("fdv", 0)),
                    "liquidity": float(p.get("liquidity", {}).get("usd", 0)),
                    "volume_24h": float(p.get("volume", {}).get("h24", 0)),
                    "volume_5m": float(p.get("volume", {}).get("m5", 0)),
                }
            except Exception:
                return {}

    def _rotate_rpc(self):
        self.current_rpc_idx = (self.current_rpc_idx + 1) % len(self.rpc_endpoints)
        logger.debug(f"Rotace RPC → {self.rpc_endpoints[self.current_rpc_idx]}")

    def _is_dupe(self, mint: str) -> bool:
        h = hashlib.sha256(mint.encode()).hexdigest()
        if h in self.dedup_set:
            return True
        return False

    def _cleanup_dedup(self):
        """M1: Periodický cleanup starých dedup záznamů."""
        now = time.time()
        expired = [k for k, ts in self.dedup_set.items() if now - ts > self.dedup_ttl]
        for k in expired:
            del self.dedup_set[k]


if __name__ == "__main__":
    from core.logging import setup_logging

    setup_logging(level_console="INFO", level_file="DEBUG")

    miner = Miner()
    try:
        asyncio.run(miner.start())
    except KeyboardInterrupt:
        logger.info("Miner ukončen uživatelem (Ctrl+C)")
    except Exception as e:
        logger.critical(f"Kritická chyba při startu Minera: {e}", exc_info=True)
