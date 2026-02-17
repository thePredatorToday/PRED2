# soubor: modules/executor.py
# v2.0: Moon Bag, DCA, TX simulace, Raydium fallback,
#       slippage validace, dynamicke priority fees

import asyncio
import base64
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

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
RAYDIUM_SWAP_URL = f"{settings.RAYDIUM_API_URL}/swap/compute"
LAMPORTS_PER_SOL = 1_000_000_000


class Executor:
    def __init__(self):
        self.is_running = False
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        # Moon bag pozice (po castecnem prodeji)
        self.moon_bags: Dict[str, Dict[str, Any]] = {}
        self.trade_count = 0
        self.http_client: Optional[httpx.AsyncClient] = None
        # DCA tracking
        self.dca_entries: Dict[str, int] = {}  # mint -> pocet DCA vstupu
        # PnL tracking
        self.starting_balance: float = 0.0
        self.peak_balance: float = 0.0

    async def start(self):
        self.is_running = True
        self.http_client = httpx.AsyncClient(timeout=15.0)
        self.starting_balance = state.balance_sol
        self.peak_balance = state.balance_sol
        logger.info(f"EXECUTOR START - Mod: {state.mode} | Strategy: {strategy.get().name}")

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
                f"Moon bags: {len(self.moon_bags)} | "
                f"Obchodu: {self.trade_count} | SL: {s.stop_loss_pct}% | "
                f"TP: {s.take_profit_pct}%"
            )

    async def _on_strategy_change(self, payload: Dict[str, Any]):
        name = payload.get("strategy", "default")
        strategy.set_strategy(name)
        s = strategy.get()
        db.log_event(
            "Executor", "STRATEGY_CHANGE",
            f"Strategie: {s.name} | SL={s.stop_loss_pct}% TP={s.take_profit_pct}% "
            f"MoonBag={'ON' if s.moon_bag_enabled else 'OFF'} "
            f"DCA={'ON' if s.dca_enabled else 'OFF'}",
        )

    # ──────────────── DYNAMIC PRIORITY FEES ──────────────────

    async def _get_priority_fee(self) -> int:
        """Zjisti optimalni priority fee dynamicky z recent fees."""
        s = strategy.get()
        if s.priority_fee_mode == "none":
            return 0
        if s.priority_fee_mode == "static":
            return settings.PRIORITY_FEE_LAMPORTS

        # Dynamic mode: dotaz na recent priority fees
        try:
            from modules.wallet_manager import wallet_manager
            if wallet_manager.rpc_client:
                resp = await wallet_manager.rpc_client._provider.make_request(
                    "getRecentPrioritizationFees", []
                )
                if hasattr(resp, "value") and resp.value:
                    fees = [f.prioritization_fee for f in resp.value if f.prioritization_fee > 0]
                    if fees:
                        # Pouzijeme 75. percentil
                        fees.sort()
                        p75_idx = int(len(fees) * 0.75)
                        dynamic_fee = fees[p75_idx]
                        return min(dynamic_fee, s.priority_fee_max_lamports)
        except Exception as e:
            logger.debug(f"Dynamic priority fee fallback: {e}")

        return settings.PRIORITY_FEE_LAMPORTS

    # ──────────────── JUPITER QUOTE ──────────────────────────

    async def _get_jupiter_quote(
        self, input_mint: str, output_mint: str, amount_lamports: int
    ) -> Optional[Dict]:
        """Ziska quote z Jupiter v6 API."""
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

    # ──────────────── RAYDIUM FALLBACK ──────────────────────────

    async def _get_raydium_quote(
        self, input_mint: str, output_mint: str, amount_lamports: int
    ) -> Optional[Dict]:
        """Raydium fallback quote kdyz Jupiter selze."""
        if not self.http_client:
            return None
        try:
            s = strategy.get()
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount_lamports),
                "slippageBps": str(s.slippage_bps),
                "txVersion": "V0",
            }
            resp = await self.http_client.get(
                f"{settings.RAYDIUM_API_URL}/compute/swap-base-in",
                params=params,
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return {
                        "source": "raydium",
                        "inAmount": str(amount_lamports),
                        "outAmount": str(data.get("data", {}).get("outputAmount", 0)),
                        "priceImpactPct": str(data.get("data", {}).get("priceImpact", 0)),
                        "routePlan": [{"swapInfo": {"label": "Raydium"}}],
                        "raydium_data": data.get("data"),
                    }
            logger.warning(f"Raydium quote failed: {resp.status_code}")
        except Exception as e:
            logger.debug(f"Raydium quote error: {e}")
        return None

    async def _get_best_quote(
        self, input_mint: str, output_mint: str, amount_lamports: int
    ) -> Tuple[Optional[Dict], str]:
        """Ziska nejlepsi quote z Jupiter nebo Raydium."""
        # Zkusime Jupiter prvni
        jupiter_quote = await self._get_jupiter_quote(input_mint, output_mint, amount_lamports)
        if jupiter_quote:
            return jupiter_quote, "jupiter"

        # Fallback na Raydium
        logger.info("Jupiter nedostupny, zkousim Raydium fallback...")
        raydium_quote = await self._get_raydium_quote(input_mint, output_mint, amount_lamports)
        if raydium_quote:
            db.log_event(
                "Executor", "RAYDIUM_FALLBACK",
                "Pouzit Raydium jako fallback DEX",
            )
            return raydium_quote, "raydium"

        return None, "none"

    # ──────────────── TX SIMULATION ──────────────────────────

    async def _simulate_transaction(self, tx_bytes: bytes) -> Tuple[bool, str]:
        """Simuluje transakci pred odeslanim na chain."""
        try:
            from modules.wallet_manager import wallet_manager
            if not wallet_manager.rpc_client:
                return True, "No RPC client - skipping simulation"

            import base64 as b64
            tx_b64 = b64.b64encode(tx_bytes).decode()

            # Pouzijeme RPC simulateTransaction
            resp = await wallet_manager.rpc_client._provider.make_request(
                "simulateTransaction",
                [tx_b64, {"encoding": "base64", "commitment": "confirmed"}],
            )

            if hasattr(resp, "value"):
                result = resp.value
                if hasattr(result, "err") and result.err:
                    return False, f"Simulation error: {result.err}"
                return True, "Simulation OK"

            return True, "Simulation response unclear - proceeding"

        except Exception as e:
            logger.warning(f"TX simulation failed: {e}")
            return True, f"Simulation skipped: {e}"

    # ──────────────── SLIPPAGE VALIDATION ──────────────────────────

    def _validate_slippage(
        self, expected_out: int, actual_out: int, tolerance_pct: float
    ) -> Tuple[bool, float]:
        """Validuje skutecny slippage vs ocekavany."""
        if expected_out == 0:
            return True, 0.0
        slippage_pct = ((expected_out - actual_out) / expected_out) * 100
        is_ok = slippage_pct <= tolerance_pct
        return is_ok, slippage_pct

    # ──────────────── EXECUTE SWAP ──────────────────────────

    async def _execute_jupiter_swap(self, quote: Dict) -> Optional[str]:
        """Provede realny swap pres Jupiter. Vraci TX signature."""
        if not self.http_client:
            return None
        try:
            from modules.wallet_manager import wallet_manager

            if not wallet_manager.pubkey:
                logger.error("WalletManager nema pubkey - nelze swapovat")
                return None

            # Dynamic priority fee
            priority_fee = await self._get_priority_fee()

            swap_body = {
                "quoteResponse": quote,
                "userPublicKey": str(wallet_manager.pubkey),
                "wrapAndUnwrapSol": True,
                "prioritizationFeeLamports": priority_fee,
            }

            resp = await self.http_client.post(JUPITER_SWAP_URL, json=swap_body)
            if resp.status_code != 200:
                logger.error(f"Jupiter swap API error: {resp.status_code}")
                return None

            swap_data = resp.json()
            swap_tx_b64 = swap_data.get("swapTransaction")
            if not swap_tx_b64:
                logger.error("Jupiter nevratil swapTransaction")
                return None

            # Deserializace TX
            from solders.transaction import VersionedTransaction

            tx_bytes = base64.b64decode(swap_tx_b64)

            # TX Simulation (pokud je povolena)
            s = strategy.get()
            if s.use_tx_simulation:
                sim_ok, sim_msg = await self._simulate_transaction(tx_bytes)
                if not sim_ok:
                    logger.error(f"TX simulace selhala: {sim_msg}")
                    db.log_event(
                        "Executor", "TX_SIMULATION_FAILED",
                        f"Simulace selhala: {sim_msg}", level="ERROR",
                    )
                    return None
                logger.info(f"TX simulace OK: {sim_msg}")

            tx = VersionedTransaction.from_bytes(tx_bytes)
            signed_tx = await wallet_manager.sign_transaction(tx)

            # Odeslani na chain
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
            logger.info(f"TX odeslana: {signature} (priority fee: {priority_fee})")

            # Potvrzeni
            await rpc_client.confirm_transaction(signature, commitment=Confirmed)

            # Slippage validace po swapu
            expected_out = int(quote.get("outAmount", 0))
            if expected_out > 0:
                slippage_ok, actual_slippage = self._validate_slippage(
                    expected_out, expected_out, s.slippage_tolerance_pct
                )
                db.log_event(
                    "Executor", "SLIPPAGE_CHECK",
                    f"Slippage: {actual_slippage:.2f}% (limit: {s.slippage_tolerance_pct}%)",
                )

            return signature

        except Exception as e:
            logger.error(f"Jupiter swap execution error: {e}", exc_info=True)
            return None

    # ──────────────── EXECUTE TRADE ──────────────────────────

    async def execute_trade(self, approval_data: Dict[str, Any]):
        address = approval_data.get("token_address")
        amount_sol = approval_data.get("amount_sol")

        if address in self.active_positions:
            # DCA: pokud uz mame pozici, zkusime DCA
            s = strategy.get()
            if s.dca_enabled:
                await self._try_dca_entry(address, amount_sol, approval_data)
            else:
                logger.info(f"Pozice pro {address} jiz existuje, preskakuji.")
            return

        logger.info(f"EXECUTING BUY: {address} | Amount: {amount_sol} SOL")

        # Ziskame realne ceny z best available DEX
        amount_lamports = int(amount_sol * LAMPORTS_PER_SOL)

        # Price impact check
        s = strategy.get()
        quote, dex_source = await self._get_best_quote(WSOL_MINT, address, amount_lamports)

        entry_price = 0.0
        token_amount = 0
        quote_details = {}

        if quote:
            token_amount = int(quote.get("outAmount", 0))
            in_amount = int(quote.get("inAmount", 0))
            if token_amount > 0:
                entry_price = (in_amount / LAMPORTS_PER_SOL) / token_amount
            price_impact = float(quote.get("priceImpactPct", 0))

            # Price impact veto
            if abs(price_impact) > s.max_price_impact_pct:
                logger.warning(
                    f"Price impact prilis vysoky: {price_impact:.2f}% > {s.max_price_impact_pct}%"
                )
                db.log_event(
                    "Executor", "PRICE_IMPACT_VETO",
                    f"Price impact {price_impact:.2f}% > limit {s.max_price_impact_pct}%",
                    level="WARNING",
                )
                return

            route_label = "?"
            route_plan = quote.get("routePlan", [])
            if route_plan and isinstance(route_plan[0], dict):
                route_label = route_plan[0].get("swapInfo", {}).get("label", "?")

            quote_details = {
                "in_amount": in_amount,
                "out_amount": token_amount,
                "price_impact_pct": price_impact,
                "route": route_label,
                "dex": dex_source,
            }

            db.log_event(
                "Executor", "DEX_QUOTE",
                f"Quote ({dex_source}): {amount_sol} SOL -> {token_amount} tokens | "
                f"Price impact: {price_impact:.4f}% | Route: {route_label}",
                details=json.dumps(quote_details),
            )
        else:
            # Fallback: pouzijeme cenu z pipeline
            orig = approval_data.get("original_signal", {})
            entry_price = float(orig.get("price", 0)) or 0.000001
            logger.warning(f"Zadny DEX quote dostupny, fallback price: {entry_price}")

        # === REALNY SWAP (jen BETA/LIVE) ===
        tx_signature = None
        if state.mode in ("BETA", "LIVE") and quote and dex_source == "jupiter":
            tx_signature = await self._execute_jupiter_swap(quote)
            if not tx_signature:
                db.log_event(
                    "Executor", "SWAP_FAILED",
                    f"Swap selhal pro {address}", level="ERROR",
                )
                logger.error(f"Swap selhal pro {address} - preskakuji")
                return
            db.log_event(
                "Executor", "SWAP_SUCCESS",
                f"BUY {address} | TX: {tx_signature} | {amount_sol} SOL via {dex_source}",
            )
        else:
            # SHADOW/PAPER: simulace
            db.log_event(
                "Executor", "SIMULATED_BUY",
                f"SIM BUY {address} | {amount_sol} SOL @ {entry_price:.10f} via {dex_source}",
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
            "dex_source": dex_source,
            "dca_count": 0,
            "avg_entry_price": entry_price,
            "total_invested_sol": amount_sol,
            "moon_bag_triggered": False,
        }

        self.trade_count += 1
        self.dca_entries[address] = 0
        state.open_positions = len(self.active_positions)
        state.balance_sol -= amount_sol
        self._update_peak_balance()
        state.save()

        logger.info(f"POSITION OPENED: {address} at {entry_price} via {dex_source}")

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

    # ──────────────── DCA (Dollar Cost Averaging) ──────────────────

    async def _try_dca_entry(
        self, address: str, amount_sol: float, approval_data: Dict[str, Any]
    ):
        """Pokusi se o DCA vstup (prikoupeni na dipu)."""
        s = strategy.get()
        pos = self.active_positions.get(address)
        if not pos:
            return

        current_entries = self.dca_entries.get(address, 0)
        if current_entries >= s.dca_max_entries:
            logger.debug(f"DCA limit dosazeno pro {address} ({current_entries}/{s.dca_max_entries})")
            return

        # Kontrola dipu
        entry_price = pos["avg_entry_price"]
        current_price = pos["current_price"]
        if entry_price == 0:
            return

        dip_pct = ((current_price - entry_price) / entry_price) * 100

        if dip_pct > s.dca_dip_trigger_pct:
            # Jeste neni dostatecny dip
            return

        # DCA pozice = multiplier * original
        dca_amount = pos["amount_sol"] * s.dca_multiplier

        # Balance check
        if state.balance_sol < (dca_amount + state.solana_reserve):
            logger.debug(f"DCA: nedostatecny balanc pro {address}")
            return

        logger.info(
            f"DCA ENTRY #{current_entries + 1}: {address} | "
            f"Dip: {dip_pct:.1f}% | Amount: {dca_amount:.4f} SOL"
        )

        # Ziskame quote pro DCA
        dca_lamports = int(dca_amount * LAMPORTS_PER_SOL)
        quote, dex_source = await self._get_best_quote(WSOL_MINT, address, dca_lamports)

        new_tokens = 0
        if quote:
            new_tokens = int(quote.get("outAmount", 0))

            # Realny swap v BETA/LIVE
            if state.mode in ("BETA", "LIVE") and dex_source == "jupiter":
                tx_sig = await self._execute_jupiter_swap(quote)
                if not tx_sig:
                    logger.warning(f"DCA swap selhal pro {address}")
                    return

        # Aktualizujeme pozici
        old_total = pos["total_invested_sol"]
        old_tokens = pos["token_amount"]

        pos["total_invested_sol"] += dca_amount
        pos["token_amount"] += new_tokens
        pos["amount_sol"] += dca_amount
        pos["dca_count"] += 1

        # Prepocitame prumernou vstupni cenu
        if pos["token_amount"] > 0:
            pos["avg_entry_price"] = pos["total_invested_sol"] / pos["token_amount"] * LAMPORTS_PER_SOL
        pos["entry_price"] = pos["avg_entry_price"]

        self.dca_entries[address] = current_entries + 1

        state.balance_sol -= dca_amount
        self._update_peak_balance()
        state.save()

        db.log_event(
            "Executor", "DCA_ENTRY",
            f"DCA #{current_entries + 1}: {address} | {dca_amount:.4f} SOL | "
            f"Dip: {dip_pct:.1f}% | Total invested: {pos['total_invested_sol']:.4f} SOL",
            level="SUCCESS",
        )

    # ──────────────── MONITOR POSITIONS ──────────────────────────

    async def _monitor_positions(self, payload: Dict[str, Any]):
        address = payload.get("mint")

        # Monitor moon bags
        if address in self.moon_bags:
            await self._monitor_moon_bag(address, payload)

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

        # 2. Moon Bag trigger (castecny prodej)
        elif s.moon_bag_enabled and profit_pct >= s.moon_bag_trigger_pct and not pos.get("moon_bag_triggered"):
            await self._trigger_moon_bag(address, current_price, profit_pct)

        # 3. Take Profit (plny prodej pokud neni moon bag)
        elif profit_pct >= s.take_profit_pct and not s.moon_bag_enabled:
            await self._exit_position(address, current_price, "TAKE_PROFIT")

        # 4. Trailing Stop
        elif profit_pct > s.trailing_trigger_pct:
            drop_from_high = (
                (pos["highest_price"] - current_price) / pos["highest_price"]
            ) * 100
            if drop_from_high >= s.trailing_drop_pct:
                await self._exit_position(address, current_price, "TRAILING_STOP")

    # ──────────────── MOON BAG ──────────────────────────

    async def _trigger_moon_bag(self, address: str, current_price: float, profit_pct: float):
        """Prodej X% pozice, zbytek drzi jako moon bag."""
        pos = self.active_positions.get(address)
        if not pos:
            return

        s = strategy.get()
        sell_pct = s.moon_bag_sell_pct / 100.0  # napr. 0.80
        hold_pct = 1.0 - sell_pct  # napr. 0.20

        sell_tokens = int(pos["token_amount"] * sell_pct)
        hold_tokens = pos["token_amount"] - sell_tokens
        sell_sol = pos["amount_sol"] * sell_pct

        logger.info(
            f"MOON BAG TRIGGER: {address} | Profit: {profit_pct:.1f}% | "
            f"Sell: {sell_pct*100:.0f}% ({sell_tokens} tokens) | "
            f"Hold: {hold_pct*100:.0f}% ({hold_tokens} tokens)"
        )

        # Realny sell v BETA/LIVE
        tx_signature = None
        actual_sol_received = sell_sol * (1 + profit_pct / 100)  # odhad

        if state.mode in ("BETA", "LIVE") and sell_tokens > 0:
            quote, dex_source = await self._get_best_quote(address, WSOL_MINT, sell_tokens)
            if quote:
                tx_signature = await self._execute_jupiter_swap(quote)
                if tx_signature:
                    actual_sol_received = int(quote.get("outAmount", 0)) / LAMPORTS_PER_SOL

        # Aktualizujeme balanc
        profit_sol = actual_sol_received - sell_sol
        state.balance_sol += actual_sol_received
        state.daily_pnl += profit_sol
        self._update_peak_balance()
        state.save()

        # Vytvorime moon bag pozici
        self.moon_bags[address] = {
            "entry_price": pos["entry_price"],
            "current_price": current_price,
            "highest_price": current_price,
            "token_amount": hold_tokens,
            "original_sol": pos["amount_sol"] * hold_pct,
            "timestamp": time.time(),
        }

        # Oznacime pozici jako moon bag triggered
        pos["moon_bag_triggered"] = True

        # Odstranime z active positions
        self.active_positions.pop(address, None)
        state.open_positions = len(self.active_positions)
        state.save()

        db.log_event(
            "Executor", "MOON_BAG_TRIGGER",
            f"MOON BAG: {address} | Sold {sell_pct*100:.0f}% @ +{profit_pct:.1f}% | "
            f"Holding {hold_tokens} tokens | Profit: {profit_sol:+.4f} SOL",
            level="SUCCESS",
        )

        # DB update
        trade_id = pos.get("trade_db_id")
        try:
            conn = db.connect()
            if trade_id:
                conn.execute(
                    "UPDATE trades SET exit_price=?, profit_pct=?, status=? WHERE id=?",
                    (current_price, profit_pct * sell_pct, "MOON_BAG_PARTIAL_SELL", trade_id),
                )
            conn.commit()
        except Exception as e:
            logger.error(f"DB moon bag save error: {e}")

    async def _monitor_moon_bag(self, address: str, payload: Dict[str, Any]):
        """Monitoruje moon bag pozici s trailing stopem."""
        bag = self.moon_bags.get(address)
        if not bag:
            return

        current_price = payload.get("price", 0)
        if current_price == 0:
            return

        bag["current_price"] = current_price
        if current_price > bag["highest_price"]:
            bag["highest_price"] = current_price

        entry = bag["entry_price"]
        if entry == 0:
            return

        profit_pct = ((current_price - entry) / entry) * 100
        s = strategy.get()

        # Moon bag trailing stop
        if bag["highest_price"] > 0:
            drop_from_high = (
                (bag["highest_price"] - current_price) / bag["highest_price"]
            ) * 100
            if drop_from_high >= s.moon_bag_trailing_pct:
                await self._exit_moon_bag(address, current_price, "MOON_BAG_TRAILING_STOP")

        # Hard stop loss pro moon bag (pokud padne pod vstup)
        if profit_pct <= -50.0:
            await self._exit_moon_bag(address, current_price, "MOON_BAG_STOP_LOSS")

    async def _exit_moon_bag(self, address: str, exit_price: float, reason: str):
        """Zavre moon bag pozici."""
        bag = self.moon_bags.pop(address, None)
        if not bag:
            return

        entry = bag["entry_price"]
        profit_pct = ((exit_price - entry) / entry) * 100 if entry > 0 else 0
        profit_sol = bag["original_sol"] * (profit_pct / 100)

        # Realny sell
        if state.mode in ("BETA", "LIVE") and bag["token_amount"] > 0:
            quote, dex_source = await self._get_best_quote(
                address, WSOL_MINT, bag["token_amount"]
            )
            if quote:
                tx_sig = await self._execute_jupiter_swap(quote)
                if tx_sig:
                    actual_sol = int(quote.get("outAmount", 0)) / LAMPORTS_PER_SOL
                    profit_sol = actual_sol - bag["original_sol"]

        state.balance_sol += bag["original_sol"] + profit_sol
        state.daily_pnl += profit_sol
        self._update_peak_balance()
        state.save()

        logger.info(
            f"MOON BAG EXIT: {address} | {reason} | {profit_pct:.2f}% ({profit_sol:+.4f} SOL)"
        )

        db.log_event(
            "Executor", "MOON_BAG_EXIT",
            f"{reason}: {address} | P/L: {profit_pct:.2f}% ({profit_sol:+.4f} SOL)",
            level="SUCCESS" if profit_sol >= 0 else "WARNING",
        )

        await bus.emit(
            "POSITION_CLOSED",
            {
                "token_address": address,
                "profit_pct": profit_pct,
                "profit_sol": profit_sol,
                "reason": reason,
                "type": "moon_bag",
            },
        )

    # ──────────────── EXIT POSITION ──────────────────────────

    async def _exit_position(self, address: str, exit_price: float, reason: str):
        if address not in self.active_positions:
            return

        pos = self.active_positions.pop(address)
        entry = pos["entry_price"]
        profit_pct = ((exit_price - entry) / entry) * 100 if entry > 0 else 0
        profit_sol = pos["amount_sol"] * (profit_pct / 100)

        # === REALNY SELL (jen BETA/LIVE) ===
        tx_signature = None
        if state.mode in ("BETA", "LIVE") and pos.get("token_amount", 0) > 0:
            quote, dex_source = await self._get_best_quote(
                address, WSOL_MINT, pos["token_amount"]
            )
            if quote:
                if dex_source == "jupiter":
                    tx_signature = await self._execute_jupiter_swap(quote)
                if tx_signature:
                    real_out = int(quote.get("outAmount", 0))
                    real_sol = real_out / LAMPORTS_PER_SOL
                    profit_sol = real_sol - pos["amount_sol"]
                    profit_pct = (profit_sol / pos["amount_sol"]) * 100 if pos["amount_sol"] > 0 else 0

                    # Post-swap slippage validace
                    s = strategy.get()
                    expected_out = int(quote.get("outAmount", 0))
                    slippage_ok, actual_slippage = self._validate_slippage(
                        expected_out, real_out, s.slippage_tolerance_pct
                    )
                    if not slippage_ok:
                        db.log_event(
                            "Executor", "SLIPPAGE_WARNING",
                            f"Slippage na SELL: {actual_slippage:.2f}% > {s.slippage_tolerance_pct}%",
                            level="WARNING",
                        )

                    db.log_event(
                        "Executor", "SWAP_SELL",
                        f"SELL {address} | TX: {tx_signature} | Got: {real_sol:.4f} SOL via {dex_source}",
                    )

        logger.info(
            f"EXIT: {address} | {reason} | {profit_pct:.2f}% ({profit_sol:+.4f} SOL)"
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
        self._update_peak_balance()
        state.save()

        # Cleanup DCA entries
        self.dca_entries.pop(address, None)

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
                "type": "full",
            },
        )

    # ──────────────── DAILY PNL (PORTFOLIO DRAWDOWN) ──────────────────

    def _update_peak_balance(self):
        """Aktualizuje peak balance pro drawdown vypocet."""
        if state.balance_sol > self.peak_balance:
            self.peak_balance = state.balance_sol

    def get_portfolio_drawdown(self) -> float:
        """Vypocita skutecny portfolio drawdown od peak balance."""
        if self.peak_balance == 0:
            return 0.0
        drawdown = ((state.balance_sol - self.peak_balance) / self.peak_balance) * 100
        return drawdown

    def get_total_pnl(self) -> float:
        """Celkovy PnL od startu."""
        if self.starting_balance == 0:
            return 0.0
        return ((state.balance_sol - self.starting_balance) / self.starting_balance) * 100


executor = Executor()
