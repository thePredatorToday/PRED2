import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx

from core import database
from core.event_bus import bus

logger = logging.getLogger(__name__)
DB: database.Database = database.Database()

FOLLOWUP_SECONDS = int(os.getenv("NOTIFIER_FOLLOWUP_SEC", "1800"))  # default 30 minutes
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")


class Notifier:
    def __init__(self):
        self._running = False
        self._tasks = set()

    async def start(self):
        if self._running:
            return
        bus.subscribe("TRADE_SIGNAL_READY", self._on_trade_signal)
        bus.subscribe("RISK_APPROVED_FOR_EXECUTION", self._on_risk_approved)
        bus.subscribe("POSITION_CLOSED", self._on_position_closed)
        bus.subscribe("RISK_VETOED", self._on_veto)
        self._running = True
        logger.info("Notifier started and subscribed to events")

    async def stop(self):
        self._running = False
        for t in list(self._tasks):
            t.cancel()
        logger.info("Notifier stopped")

    async def _on_trade_signal(self, payload: Dict[str, Any]):
        try:
            mint = payload.get("mint")
            symbol = payload.get("symbol", "?")
            score = float(payload.get("score", 0))
            current_mcap = float(payload.get("market_cap", 0) or 0)
            current_price = float(payload.get("price", 0) or 0)

            # Simple heuristic: predicted growth scales with score (safe cap)
            growth_pct = min(10.0, max(0.05, score / 20.0))  # e.g., score 50 -> 2.5 -> 250%? cap to 10x => use percent
            predicted_mcap = current_mcap * (1.0 + growth_pct)
            predicted_price = current_price * (1.0 + growth_pct)

            predicted_at = datetime.utcnow().isoformat()

            record = {
                "mint": mint,
                "symbol": symbol,
                "predicted_mcap": predicted_mcap,
                "predicted_price": predicted_price,
                "predicted_at": predicted_at,
            }
            pid = DB.save_prediction(record)

            message = (
                f"Signal: {symbol} ({mint})\n"
                f"Score: {score:.1f} | Now MC: {current_mcap:.0f} | Now price: {current_price:.6f}\n"
                f"Prediction (in {FOLLOWUP_SECONDS//60}m): MC -> {predicted_mcap:.0f}, price -> {predicted_price:.6f}\n"
                f"Signal id: {pid}"
            )

            await self._send_broadcast(message)

            # schedule follow-up
            task = asyncio.create_task(self._schedule_followup(pid, mint, symbol, predicted_mcap, predicted_price))
            self._tasks.add(task)
            task.add_done_callback(lambda t: self._tasks.discard(t))

        except Exception as e:
            logger.exception(f"Notifier failed handling trade signal: {e}")

    async def _on_risk_approved(self, payload: Dict[str, Any]):
        symbol = payload.get("symbol", "?")
        mint = payload.get("mint")
        message = f"RISK OK: Execution approved for {symbol} ({mint})"
        await self._send_broadcast(message)

    async def _on_position_closed(self, payload: Dict[str, Any]):
        symbol = payload.get("symbol", "?")
        pnl = payload.get("pnl")
        message = f"Position closed: {symbol} | PnL: {pnl}"
        await self._send_broadcast(message)

    async def _on_veto(self, payload: Dict[str, Any]):
        symbol = payload.get("symbol", "?")
        reason = payload.get("reason", "vetoed by risk")
        message = f"VETO: {symbol} | {reason}"
        await self._send_broadcast(message)

    async def _schedule_followup(self, prediction_id: int, mint: str, symbol: str, predicted_mcap: float, predicted_price: float):
        try:
            await asyncio.sleep(FOLLOWUP_SECONDS)

            # fetch current market data from DexScreener (best-effort)
            actual_mcap = None
            actual_price = None
            result_text = "unknown"

            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
                    r = await client.get(url)
                    if r.status_code == 200:
                        j = r.json()
                        pair = j.get("pairs", [None])[0]
                        if pair:
                            actual_price = float(pair.get("priceUsd") or 0)
                            # Dexscreener does not directly provide market cap; attempt derived if supply known
                            market_cap = pair.get("marketCap") or None
                            if market_cap:
                                actual_mcap = float(market_cap)
            except Exception:
                logger.exception("Failed to fetch market data for follow-up")

            if actual_mcap is None and actual_price is None:
                result_text = "no-data"
            else:
                # Decide success: actual_mcap >= predicted_mcap * 0.9
                ok = False
                if actual_mcap and predicted_mcap:
                    ok = actual_mcap >= predicted_mcap * 0.9
                elif actual_price and predicted_price:
                    ok = actual_price >= predicted_price * 0.9
                result_text = "hit" if ok else "miss"

            DB.update_prediction_result(prediction_id, {
                "actual_mcap": actual_mcap,
                "actual_price": actual_price,
                "result": result_text,
            })

            follow_msg = (
                f"Follow-up [{prediction_id}]: {symbol} ({mint})\n"
                f"Predicted MC: {predicted_mcap:.0f} | Predicted price: {predicted_price:.6f}\n"
                f"Actual MC: {actual_mcap if actual_mcap is not None else 'n/a'} | Actual price: {actual_price if actual_price is not None else 'n/a'}\n"
                f"Result: {result_text}"
            )
            await self._send_broadcast(follow_msg)

        except asyncio.CancelledError:
            logger.info(f"Follow-up task for prediction {prediction_id} cancelled")
        except Exception:
            logger.exception("Error in follow-up task")

    async def _send_broadcast(self, message: str):
        # Always log locally
        logger.info(f"Notifier broadcast:\n{message}")

        # Telegram
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            await self._send_telegram(message)
        else:
            logger.debug("Telegram not configured (dry-run)")

        # X (Twitter) - using simple tweet POST
        if X_BEARER_TOKEN:
            await self._send_x(message)
        else:
            logger.debug("X (Twitter) not configured (dry-run)")

    async def _send_telegram(self, message: str):
        try:
            api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(api, json={"chat_id": TELEGRAM_CHAT_ID, "text": message})
        except Exception:
            logger.exception("Failed sending Telegram message")

    async def _send_x(self, message: str):
        try:
            api = "https://api.twitter.com/2/tweets"
            headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(api, json={"text": message}, headers=headers)
        except Exception:
            logger.exception("Failed sending X message")


notifier = Notifier()
