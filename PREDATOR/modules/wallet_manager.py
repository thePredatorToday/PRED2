# soubor: modules/wallet_manager.py
# WalletManager: Správa peněženky, podepisování, monitorování balancu
# Bezpečnost: Klíče pouze v ENV, nikdy ne v logách
#
# Opravy:
# - B6: sign_transaction API kompatibilita (solders VersionedTransaction)
# - SHADOW/PAPER mód tolerantní – necrashuje bez WALLET_PRIVATE_KEY

import asyncio
import base58
import logging
import os
import time
from typing import Optional, Tuple

from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from core.event_bus import bus
from core.config import settings
from core.system_state import state

load_dotenv()

logger = logging.getLogger("Predator.WalletManager")


class WalletManager:
    """
    Správa Solana peněženky.
    - Kontrola balancu
    - Podepisování TX
    - Anti-drain kontroly
    """

    def __init__(self):
        self.is_running = False
        self.rpc_url = os.getenv("SOLANA_RPC_URL", settings.SOLANA_RPC_URL)
        self.rpc_fallback = os.getenv(
            "SOLANA_RPC_FALLBACK", settings.SOLANA_RPC_FALLBACK or settings.SOLANA_RPC_URL
        )
        self.rpc_client: Optional[AsyncClient] = None
        self.keypair = None
        self.pubkey = None
        self.last_balance_check = 0
        self.cached_balance = 0.0
        self.transaction_count = 0
        self.failed_transactions = 0
        self.reserve_sol = settings.SOLANA_RESERVE

    async def start(self):
        """Spustí WalletManager – monitorování balancu."""
        self.is_running = True

        private_key_b58 = os.getenv("WALLET_PRIVATE_KEY", "")

        # B6: V SHADOW/PAPER módu necrashujeme bez klíče
        if not private_key_b58:
            if state.mode in ("SHADOW", "PAPER"):
                logger.warning(
                    "WALLET_PRIVATE_KEY neni nastavena – WalletManager bezi v simulacnim modu"
                )
                return
            else:
                logger.critical("WALLET_PRIVATE_KEY neni nastavena v .env!")
                raise ValueError("WALLET_PRIVATE_KEY missing pro BETA/LIVE mod")

        try:
            private_key_bytes = base58.b58decode(private_key_b58)
            self.keypair = Keypair.from_bytes(private_key_bytes)
            self.pubkey = self.keypair.pubkey()
            logger.info(
                f"Penezenka nactena: {str(self.pubkey)[:8]}...{str(self.pubkey)[-8:]}"
            )
        except Exception as e:
            logger.critical(f"Chyba pri nacitani penezenky: {e}")
            raise

        safe_url = self.rpc_url.split("?")[0] if "?" in self.rpc_url else self.rpc_url
        logger.info(f"WALLET MANAGER START – RPC: {safe_url}")

        self.rpc_client = AsyncClient(self.rpc_url)
        connected = await self.rpc_client.is_connected()
        if not connected:
            safe_fallback = (
                self.rpc_fallback.split("?")[0]
                if "?" in self.rpc_fallback
                else self.rpc_fallback
            )
            logger.warning(f"RPC fallback: {safe_fallback}")
            self.rpc_client = AsyncClient(self.rpc_fallback)

        asyncio.create_task(self._check_balance_loop())
        asyncio.create_task(self._heartbeat())

    async def stop(self):
        """Zastaví WalletManager."""
        self.is_running = False
        if self.rpc_client:
            await self.rpc_client.close()
        logger.info("WalletManager zastaven")

    async def _heartbeat(self):
        """Pravidelné hlášení stavu peněženky."""
        while self.is_running:
            await asyncio.sleep(60)
            logger.info(
                f"WalletManager aktivni | Balanc: {self.cached_balance:.4f} SOL | "
                f"TX: {self.transaction_count} | Failed: {self.failed_transactions}"
            )

    async def _check_balance_loop(self):
        """Periodicky kontroluje balanc peněženky."""
        while self.is_running:
            await asyncio.sleep(30)
            try:
                balance = await self.get_balance()
                if balance != self.cached_balance:
                    logger.debug(
                        f"Balanc update: {self.cached_balance:.4f} -> {balance:.4f} SOL"
                    )
                    self.cached_balance = balance
                    state.balance_sol = balance
                    from datetime import datetime

                    state.last_update = datetime.utcnow()
                    state.save()
            except Exception as e:
                logger.warning(f"Chyba pri kontrole balancu: {e}")

    async def get_balance(self) -> float:
        """Vrátí aktuální balanc v SOL (cached 10s)."""
        now = time.time()
        if now - self.last_balance_check < 10:
            return self.cached_balance

        if not self.rpc_client or not self.pubkey:
            return self.cached_balance

        try:
            response = await self.rpc_client.get_balance(self.pubkey)
            balance_lamports = response.value
            balance_sol = balance_lamports / 1e9
            self.cached_balance = balance_sol
            self.last_balance_check = now
            return balance_sol
        except Exception as e:
            logger.error(f"Chyba pri RPC get_balance: {e}")
            return self.cached_balance

    # B6: Opravené API – solders Transaction.sign() bere list keypairs
    async def sign_transaction(self, transaction) -> Any:
        """
        Podepisuje TX privátním klíčem.
        Kompatibilní s solders Transaction i VersionedTransaction.
        """
        if not self.keypair:
            raise RuntimeError("WalletManager nema nacteny keypair")
        try:
            transaction.sign([self.keypair])
            self.transaction_count += 1
            logger.debug(f"TX podepsana (#{self.transaction_count})")
            return transaction
        except TypeError:
            # Fallback pro starší API kde sign bere *args
            transaction.sign(self.keypair)
            self.transaction_count += 1
            return transaction
        except Exception as e:
            self.failed_transactions += 1
            logger.error(f"Chyba pri podepisovani TX: {e}")
            raise

    async def check_anti_drain(self, amount_sol: float) -> Tuple[bool, str]:
        """Anti-drain check. Vrátí (allowed, reason)."""
        required_balance = amount_sol + self.reserve_sol
        if self.cached_balance < required_balance:
            return False, (
                f"Nedostatecny balanc (potreba {required_balance:.4f} SOL, "
                f"mame {self.cached_balance:.4f})"
            )

        max_per_tx = self.cached_balance * 0.5
        if amount_sol > max_per_tx:
            return False, f"TX prekracuje max % z balancu (max {max_per_tx:.4f} SOL)"

        return True, "OK"

    async def estimate_tx_cost(self) -> float:
        """Odhad transakčních poplatků v SOL."""
        return 0.0001


# Singleton instance
wallet_manager = WalletManager()
