# soubor: modules/executor.py
# Executor s Jupiter swap integrací:
# - SHADOW/PAPER: Simulace s reálnými cenami z Jupiter quote API
# - BETA/LIVE: Reálný swap přes Jupiter + wallet signing
# - Dynamická strategie (reaguje na STRATEGY_CHANGE)

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from core.event_bus import bus
from core.system_state import state
from core.database import db
from core.config import settings
from core.strategy import strategy

logger = logging.getLogger("Predator.Executor")

# SOL mint address (wrapped SOL)
WSOL_MINT = "So11111111111111111111111111111111111111112"
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
LAMPORTS_PER_SOL = 1_000_000_000


class Executor:
    def __init__(self):
        self.is_running = False
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        self.trade_count = 0
        self.http_client: Optional[httpx.AsyncClient] = None

    async def start(self):
        self.is_running = True
        self.http_client = httpx.AsyncClient(timeout=15.0)
        logger.info(f"EXECUTOR START – Mod: {state.mode} | Strategy: {strategy.get().name}")

        bus.subscribe("RISK_APPROVED_FOR_EXECUTION", self.execute_trade)
        bus.subscribe("COIN_UPDATE", self._monitor_positions)
        bus.subscribe("STRATEGY_CHANGE", self._on_strategy_change)

        asyncio.create_task(self._heartbeat())

        db.log_event("Executor", "MODULE_START", f"Executor spusten v modu {state.mode}")

    async def stop(self):
        self.is_running = False
        if self.http_client:
            await self.http_client.aclose()
        logger.info("Executor zastaven")

    async def _heartbeat(self):
        while self.is_running:
            await asyncio.sleep(60)
            s = strategy.get()
            logger.info(
                f"Executor | Pozice: {len(self.active_positions)} | "
                f"Obchodu: {self.trade_count} | SL: {s.stop_loss_pct}% | "
                f"TP: {s.take_profit_pct}%"
            )

    async def _on_strategy_change(self, payload: Dict[str, Any]):
        name = payload.get("strategy", "default")
        strategy.set_strategy(name)
        s = strategy.get()
        db.log_event(
            "Executor", "STRATEGY_CHANGE",
            f"Strategie: {s.name} | SL={s.stop_loss_pct}% TP={s.take_profit_pct}%",
        )

    # ────────────────────────── JUPITER QUOTE ──────────────────────────

    async def _get_jupiter_quote(
        self, input_mint: str, output_mint: str, amount_lamports: int
    ) -> Optional[Dict]:
        """Získá quote z Jupiter v6 API."""
        if not self.http_client:
            return None
        try:
            s = strategy.get()
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount_lamports),
                "slippageBps": str(s.slippage_bps),
                "onlyDirectRoutes": "false",
            }
            resp = await self.http_client.get(JUPITER_QUOTE_URL, params=params)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"Jupiter quote failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Jupiter quote error: {e}")
        return None

    async def _execute_jupiter_swap(self, quote: Dict) -> Optional[str]:
        """Provede reálný swap přes Jupiter. Vrací TX signature."""
        if not self.http_client:
            return None
        try:
            from modules.wallet_manager import wallet_manager

            if not wallet_manager.pubkey:
                logger.error("WalletManager nema pubkey – nelze swapovat")
                return None

            swap_body = {
                "quoteResponse": quote,
                "userPublicKey": str(wallet_manager.pubkey),
                "wrapAndUnwrapSol": True,
                "prioritizationFeeLamports": settings.PRIORITY_FEE_LAMPORTS,
            }

            resp = await self.http_client.post(JUPITER_SWAP_URL, json=swap_body)
            if resp.status_code != 200:
                logger.error(f"Jupiter swap API error: {resp.status_code}")
                return None

            swap_data = resp.json()
            swap_tx_b64 = swap_data.get("swapTransaction")
            if not swap_tx_b64:
                logger.error("Jupiter nevrátil swapTransaction")
                return None

            # Deserializace a podepsání TX
            import base64
            from solders.transaction import VersionedTransaction

            tx_bytes = base64.b64decode(swap_tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)
            signed_tx = await wallet_manager.sign_transaction(tx)

            # Odeslání na chain
            tx_bytes_signed = bytes(signed_tx)
            rpc_client = wallet_manager.rpc_client
            if not rpc_client:
                logger.error("WalletManager nema RPC klienta")
                return None

            from solana.rpc.commitment import Confirmed

            send_resp = await rpc_client.send_raw_transaction(
                tx_bytes_signed, opts={"skip_preflight": True}
            )
            signature = str(send_resp.value)
            logger.info(f"TX odeslana: {signature}")

            # Počkáme na potvrzení
            await rpc_client.confirm_transaction(signature, commitment=Confirmed)
            return signature

        except Exception as e:
            logger.error(f"Jupiter swap execution error: {e}", exc_info=True)
            return None

    # ────────────────────────── EXECUTE TRADE ──────────────────────────

    async def execute_trade(self, approval_data: Dict[str, Any]):
        address = approval_data.get("token_address")
        amount_sol = approval_data.get("amount_sol")

        if address in self.active_positions:
            logger.info(f"Pozice pro {address} jiz existuje, preskakuji.")
            return

        logger.info(f"EXECUTING BUY: {address} | Amount: {amount_sol} SOL")

        # Získáme reálnou cenu z Jupiter quote
        amount_lamports = int(amount_sol * LAMPORTS_PER_SOL)
        quote = await self._get_jupiter_quote(WSOL_MINT, address, amount_lamports)

        entry_price = 0.0
        token_amount = 0
        quote_details = {}

        if quote:
            # Jupiter vrací outAmount (kolik tokenů dostaneme)
            token_amount = int(quote.get("outAmount", 0))
            in_amount = int(quote.get("inAmount", 0))
            # Cena = SOL zaplaceno / tokeny získáno
            if token_amount > 0:
                entry_price = (in_amount / LAMPORTS_PER_SOL) / token_amount
            price_impact = float(quote.get("priceImpactPct", 0))

            quote_details = {
                "in_amount": in_amount,
                "out_amount": token_amount,
                "price_impact_pct": price_impact,
                "route": quote.get("routePlan", [{}])[0].get("swapInfo", {}).get("label", "?"),
            }

            db.log_event(
                "Executor", "JUPITER_QUOTE",
                f"Quote: {amount_sol} SOL -> {token_amount} tokens | "
                f"Price impact: {price_impact:.4f}% | Route: {quote_details['route']}",
                details=json.dumps(quote_details),
            )
        else:
            # Fallback: použijeme cenu z pipeline
            orig = approval_data.get("original_signal", {})
            entry_price = float(orig.get("price", 0)) or 0.000001
            logger.warning(f"Jupiter quote nedostupny, fallback price: {entry_price}")

        # === REÁLNÝ SWAP (jen BETA/LIVE) ===
        tx_signature = None
        if state.mode in ("BETA", "LIVE") and quote:
            tx_signature = await self._execute_jupiter_swap(quote)
            if not tx_signature:
                db.log_event(
                    "Executor", "SWAP_FAILED",
                    f"Swap selhal pro {address}", level="ERROR",
                )
                logger.error(f"Swap selhal pro {address} – preskakuji")
                return
            db.log_event(
                "Executor", "SWAP_SUCCESS",
                f"BUY {address} | TX: {tx_signature} | {amount_sol} SOL",
            )
        else:
            # SHADOW/PAPER: simulace
            db.log_event(
                "Executor", "SIMULATED_BUY",
                f"SIM BUY {address} | {amount_sol} SOL @ {entry_price:.10f}",
                details=json.dumps(quote_details) if quote_details else None,
            )

        self.active_positions[address] = {
            "entry_price": entry_price,
            "current_price": entry_price,
            "amount_sol": amount_sol,
            "token_amount": token_amount,
            "timestamp": time.time(),
            "highest_price": entry_price,
            "status": "OPEN",
            "trade_db_id": None,
            "tx_signature": tx_signature,
        }

        self.trade_count += 1
        state.open_positions = len(self.active_positions)
        state.balance_sol -= amount_sol
        state.save()

        logger.info(f"POSITION OPENED: {address} at {entry_price}")

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
            logger.error(f"DB trade save error: {e}")

    # ────────────────────────── MONITOR ──────────────────────────

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
        s = strategy.get()

        # 1. Stop Loss
        if profit_pct <= s.stop_loss_pct:
            await self._exit_position(address, current_price, "STOP_LOSS")

        # 2. Take Profit
        elif profit_pct >= s.take_profit_pct:
            await self._exit_position(address, current_price, "TAKE_PROFIT")

        # 3. Trailing Stop
        elif profit_pct > s.trailing_trigger_pct:
            drop_from_high = (
                (pos["highest_price"] - current_price) / pos["highest_price"]
            ) * 100
            if drop_from_high >= s.trailing_drop_pct:
                await self._exit_position(address, current_price, "TRAILING_STOP")

    # ────────────────────────── EXIT ──────────────────────────

    async def _exit_position(self, address: str, exit_price: float, reason: str):
        if address not in self.active_positions:
            return

        pos = self.active_positions.pop(address)
        entry = pos["entry_price"]
        profit_pct = ((exit_price - entry) / entry) * 100 if entry > 0 else 0
        profit_sol = pos["amount_sol"] * (profit_pct / 100)

        # === REÁLNÝ SELL (jen BETA/LIVE) ===
        tx_signature = None
        if state.mode in ("BETA", "LIVE") and pos.get("token_amount", 0) > 0:
            # Prodáme tokeny zpět za SOL
            quote = await self._get_jupiter_quote(
                address, WSOL_MINT, pos["token_amount"]
            )
            if quote:
                tx_signature = await self._execute_jupiter_swap(quote)
                if tx_signature:
                    # Aktualizujeme reálný profit z quote
                    real_out = int(quote.get("outAmount", 0))
                    real_sol = real_out / LAMPORTS_PER_SOL
                    profit_sol = real_sol - pos["amount_sol"]
                    profit_pct = (profit_sol / pos["amount_sol"]) * 100 if pos["amount_sol"] > 0 else 0
                    db.log_event(
                        "Executor", "SWAP_SELL",
                        f"SELL {address} | TX: {tx_signature} | Got: {real_sol:.4f} SOL",
                    )

        logger.info(
            f"EXIT: {address} | {reason} | {profit_pct:.2f}% ({profit_sol:.4f} SOL)"
        )

        db.log_event(
            "Executor",
            "POSITION_CLOSED",
            f"{reason}: {address} | P/L: {profit_pct:.2f}% ({profit_sol:+.4f} SOL)",
            level="SUCCESS" if profit_sol >= 0 else "WARNING",
        )

        state.open_positions = len(self.active_positions)
        state.balance_sol += pos["amount_sol"] + profit_sol
        state.daily_pnl += profit_sol
        state.save()

        trade_id = pos.get("trade_db_id")
        try:
            conn = db.connect()
            if trade_id:
                conn.execute(
                    "UPDATE trades SET exit_price=?, profit_pct=?, status=? WHERE id=?",
                    (exit_price, profit_pct, f"CLOSED_{reason}", trade_id),
                )
            else:
                conn.execute(
                    "INSERT INTO trades (mint, entry_price, exit_price, profit_pct, status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (address, entry, exit_price, profit_pct, f"CLOSED_{reason}"),
                )
            conn.commit()
        except Exception as e:
            logger.error(f"DB exit save error: {e}")

        await bus.emit(
            "POSITION_CLOSED",
            {
                "token_address": address,
                "profit_pct": profit_pct,
                "profit_sol": profit_sol,
                "reason": reason,
                "tx_signature": tx_signature,
            },
        )


executor = Executor()
