# soubor: modules/wallet_manager.py
# WalletManager: Správa peněženky, podepisování, monitorování balancu
# Bezpečnost: Klíče pouze v ENV, nikdy ne v logách

import asyncio
import base58
import logging
import os
from typing import Optional, Tuple

from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction

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
    - Hardware wallet kompatibilita (preview)
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
        self.reserve_sol = settings.SOLANA_RESERVE if hasattr(settings, 'SOLANA_RESERVE') else 0.005

    async def start(self):
        """Spustí WalletManager – monitorování balancu."""
        self.is_running = True

        # Načteme privátní klíč (NIKDY ne v logech!)
        private_key_b58 = os.getenv("WALLET_PRIVATE_KEY", "")
        if not private_key_b58:
            logger.critical("❌ WALLET_PRIVATE_KEY není nastavena v .env!")
            logger.info("💡 V SHADOW módu můžeš použít dummy hodnotu.")
            raise ValueError("WALLET_PRIVATE_KEY missing")

        try:
            # Korektní dekódování z base58
            private_key_bytes = base58.b58decode(private_key_b58)
            self.keypair = Keypair.from_secret_key(private_key_bytes)
            self.pubkey = self.keypair.pubkey()
            logger.info(
                f"✅ Peněženka načtena: {str(self.pubkey)[:8]}...{str(self.pubkey)[-8:]}"
            )
        except Exception as e:
            logger.critical(f"❌ Chyba při načítání peněženky: {e}")
            raise

        safe_url = self.rpc_url.split("?")[0] if "?" in self.rpc_url else self.rpc_url
        logger.info(f"💳 WALLET MANAGER START – RPC: {safe_url}")

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

        # Periodická kontrola balancu
        asyncio.create_task(self._check_balance_loop())
        asyncio.create_task(self._heartbeat())

    async def stop(self):
        """Zastaví WalletManager."""
        self.is_running = False
        if self.rpc_client:
            await self.rpc_client.close()
        logger.info("🛑 WalletManager zastaven")

    async def _heartbeat(self):
        """Pravidelné hlášení stavu peněženky."""
        while self.is_running:
            await asyncio.sleep(60)
            logger.info(
                f"💳 WalletManager aktivní | Balanc: {self.cached_balance:.4f} SOL | "
                f"TX: {self.transaction_count} | Failed: {self.failed_transactions}"
            )

    async def _check_balance_loop(self):
        """Periodicky kontroluje balanc peněženky."""
        while self.is_running:
            await asyncio.sleep(30)  # Každých 30s
            try:
                balance = await self.get_balance()
                if balance != self.cached_balance:
                    logger.debug(f"💳 Balanc update: {self.cached_balance:.4f} → {balance:.4f} SOL")
                    self.cached_balance = balance
                    state.balance_sol = balance
                    state.last_update = __import__("datetime").datetime.utcnow()
                    state.save()
            except Exception as e:
                logger.warning(f"Chyba při kontrole balancu: {e}")

    async def get_balance(self) -> float:
        """
        Vrátí aktuální balanc v SOL.
        Cached – RPC se volá max každých 10s.
        """
        import time

        now = time.time()
        if now - self.last_balance_check < 10:
            return self.cached_balance

        if not self.rpc_client:
            return self.cached_balance

        try:
            response = await self.rpc_client.get_balance(self.pubkey)
            balance_lamports = response.value
            balance_sol = balance_lamports / 1e9  # Lamports to SOL
            self.cached_balance = balance_sol
            self.last_balance_check = now
            return balance_sol
        except Exception as e:
            logger.error(f"Chyba při RPC get_balance: {e}")
            return self.cached_balance

    async def sign_transaction(self, transaction: Transaction) -> Transaction:
        """
        Podepisuje TX privátním klíčem.
        BEZPEČNOST: Volaj jen pro schválené TX!
        """
        try:
            # Signus TX
            transaction.sign(self.keypair)
            self.transaction_count += 1
            logger.debug(f"✅ TX podepsána (#{self.transaction_count})")
            return transaction
        except Exception as e:
            self.failed_transactions += 1
            logger.error(f"❌ Chyba při podepisování TX: {e}")
            raise

    async def check_anti_drain(self, amount_sol: float) -> Tuple[bool, str]:
        """
        Anti-drain check.
        Vrátí (allowed, reason).
        """
        # 1. Kontrola rezervy
        required_balance = amount_sol + self.reserve_sol
        if self.cached_balance < required_balance:
            return False, f"Nedostatečný balanc (potřeba {required_balance:.4f} SOL, máme {self.cached_balance:.4f})"

        # 2. Max % z celkového balancu per transakce (max 50%)
        max_per_tx = self.cached_balance * 0.5
        if amount_sol > max_per_tx:
            return False, f"TX překračuje max % z balancu (max {max_per_tx:.4f} SOL)"

        # 3. Ověření že nejde o neznámou adresu (v reálu bychom kontrolovali allowlist)
        return True, "OK"

    async def estimate_tx_cost(self) -> float:
        """
        Odhad transakčních poplatků v SOL.
        Tip: 0.00005-0.0005 SOL pro normální TX.
        """
        # V reálu by se to počítalo z aktuálního network state
        return 0.0001  # Placeholder estimate


# Singleton instance
wallet_manager = WalletManager()
