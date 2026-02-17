# soubor: modules/hunter.py
# + Dynamická strategie (STRATEGY_CHANGE event)
# + Event logging do DB pro dashboard

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey
from tenacity import retry, stop_after_attempt, wait_exponential

from core.event_bus import bus
from core.database import db
from core.strategy import strategy

load_dotenv()

logger = logging.getLogger("Predator.Hunter")


class HunterPayload(BaseModel):
    mint: str = Field(..., description="Token mint address")
    name: str = Field(default="Unknown", description="Token name")
    symbol: str = Field(default="?", description="Token symbol")
    price: float = Field(default=0.0, ge=0)
    market_cap: float = Field(default=0.0, ge=0)
    liquidity: float = Field(default=0.0, ge=0)
    volume_24h: float = Field(default=0.0, ge=0)
    volume_5m: float = Field(default=0.0, ge=0)
    hunter_score: Optional[float] = None


class HunterModel(BaseModel):
    weights: Dict[str, float] = Field(
        default_factory=lambda: {
            "liquidity": 0.30,
            "volume": 0.30,
            "market_cap": 0.30,
            "holders": 0.10,
        },
    )
    thresholds: Dict[str, float] = Field(
        default_factory=lambda: {
            "min_liquidity": 2000.0,
            "min_volume_24h": 1000.0,
            "max_market_cap": 1000000.0,
            "min_holders": 50,
            "max_top10_holders_pct": 0.50,
        },
    )


class Hunter:
    def __init__(self):
        self.model = HunterModel()
        self.rpc_url = os.getenv(
            "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
        )
        self.rpc_fallback = os.getenv(
            "SOLANA_RPC_FALLBACK", "https://api.mainnet-beta.solana.com"
        )
        self.rpc_client: Optional[AsyncClient] = None
        self.processed_count = 0
        self.early_rejected = 0
        self.rpc_rejected = 0
        self.selected_count = 0
        self.max_prelim_score = 0.0
        self.pending_low_liq = {}

    async def start(self):
        safe_url = self.rpc_url.split("?")[0] if "?" in self.rpc_url else self.rpc_url
        self.rpc_client = AsyncClient(self.rpc_url)
        connected = await self.rpc_client.is_connected()
        if not connected:
            logger.warning(f"Primarni RPC selhal -> fallback")
            self.rpc_client = AsyncClient(self.rpc_fallback)
            connected = await self.rpc_client.is_connected()
        if not connected:
            raise RuntimeError("Zadny dostupny RPC – Hunter offline")
        logger.info(f"HUNTER START – RPC pripojen ({safe_url})")
        asyncio.create_task(self._heartbeat())
        bus.subscribe("NEW_COIN_FOUND", self.evaluate)
        bus.subscribe("COIN_UPDATE", self.evaluate)
        bus.subscribe("STRATEGY_CHANGE", self._on_strategy_change)

        db.log_event("Hunter", "MODULE_START", "Hunter spusten a pripojen k RPC")

    async def stop(self):
        if self.rpc_client:
            await self.rpc_client.close()
        logger.info("Hunter zastaven")

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(60)
            s = strategy.get()
            logger.info(
                f"Hunter | Zpracovano: {self.processed_count} | "
                f"Reject: {self.early_rejected} | RPC reject: {self.rpc_rejected} | "
                f"Selected: {self.selected_count} | Strategy: {s.name}"
            )

    async def _on_strategy_change(self, payload: Dict[str, Any]):
        name = payload.get("strategy", "default")
        strategy.set_strategy(name)
        s = strategy.get()
        # Aktualizujeme thresholds modelu podle strategie
        self.model.thresholds["min_liquidity"] = s.min_liquidity
        self.model.thresholds["min_volume_24h"] = s.min_volume_24h
        db.log_event(
            "Hunter", "STRATEGY_CHANGE",
            f"Strategie: {s.name} | min_liq={s.min_liquidity} | min_score={s.min_final_score}",
        )

    async def evaluate(self, payload: Dict[str, Any]):
        self.processed_count += 1

        try:
            validated = HunterPayload(**payload)
        except ValidationError:
            self.early_rejected += 1
            return

        prelim_score = self._calculate_preliminary_score(validated)
        self.max_prelim_score = max(self.max_prelim_score, prelim_score)

        if validated.liquidity < 1000:
            mint = validated.mint
            if mint in self.pending_low_liq:
                if time.time() - self.pending_low_liq[mint] < 30:
                    return
            self.pending_low_liq[mint] = time.time()
            return

        # Dynamický threshold ze strategie
        s = strategy.get()
        min_score_threshold = s.min_prelim_score

        if prelim_score < min_score_threshold:
            self.early_rejected += 1
            return

        try:
            mint_pubkey = Pubkey.from_string(validated.mint)
            mint_auth, freeze_auth, holder_count, top10_pct = await self._rpc_audit_mint(
                mint_pubkey
            )

            reject_reason = None
            if mint_auth is not None:
                reject_reason = "Mint authority stale aktivni"
            elif freeze_auth is not None:
                reject_reason = "Freeze authority stale aktivni"
            elif holder_count < self.model.thresholds["min_holders"]:
                reject_reason = f"Prilis malo holderu ({holder_count})"
            elif top10_pct > self.model.thresholds["max_top10_holders_pct"]:
                reject_reason = f"Koncentrace top10: {top10_pct * 100:.1f}%"

            if reject_reason:
                self.rpc_rejected += 1
                db.log_event(
                    "Hunter", "REJECT",
                    f"{validated.name} ({validated.mint[:8]}...) | {reject_reason}",
                )
                return

            holders_bonus = 15 if holder_count > 200 else 0
            final_score = prelim_score + holders_bonus

            # Dynamický final threshold ze strategie
            if final_score > s.min_final_score:
                validated.hunter_score = final_score
                await bus.emit("GOOD_COIN_SELECTED", validated.dict())
                self.selected_count += 1
                db.log_event(
                    "Hunter", "GOOD_COIN",
                    f"SELECTED: {validated.name} | score={final_score:.1f} | "
                    f"holders={holder_count} | liq=${validated.liquidity:.0f}",
                    level="SUCCESS",
                )
            else:
                self.rpc_rejected += 1

        except Exception as e:
            logger.error(f"RPC audit selhal pro {validated.mint}: {e}")
            self.rpc_rejected += 1

    def _calculate_preliminary_score(self, p: HunterPayload) -> float:
        w = self.model.weights
        t = self.model.thresholds
        liq_score = min(100, (p.liquidity / t["min_liquidity"]) * 100) * w["liquidity"]
        vol_score = min(100, (p.volume_24h / t["min_volume_24h"]) * 100) * w["volume"]
        mc_score = (
            min(100, (t["max_market_cap"] - p.market_cap) / t["max_market_cap"] * 100)
            * w["market_cap"]
        )
        return liq_score + vol_score + mc_score

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15)
    )
    async def _rpc_audit_mint(
        self, mint_pubkey: Pubkey
    ) -> Tuple[Optional[Pubkey], Optional[Pubkey], int, float]:
        acc_info = await self.rpc_client.get_account_info(
            mint_pubkey, commitment=Confirmed
        )
        if not acc_info.value:
            raise ValueError(f"Mint {mint_pubkey} not found")

        data = acc_info.value.data
        mint_auth = Pubkey(data[4:36]) if data[4:8] != b"\x00\x00\x00\x00" else None
        freeze_auth = (
            Pubkey(data[36:68]) if data[36:40] != b"\x00\x00\x00\x00" else None
        )

        largest_resp = await self.rpc_client.get_token_largest_accounts(mint_pubkey)
        largest = largest_resp.value

        holder_count = len(largest)
        total_in_largest = sum(int(acc.amount) for acc in largest)
        top10_supply = sum(int(acc.amount) for acc in largest[:10])
        top10_pct = top10_supply / total_in_largest if total_in_largest > 0 else 0.0

        return mint_auth, freeze_auth, holder_count, top10_pct

    def update_model(self, new_model: Dict[str, Any]):
        try:
            updated = HunterModel(**new_model)
            self.model = updated
        except ValidationError as e:
            logger.error(f"Nevalidni update modelu: {e}")


hunter = Hunter()
