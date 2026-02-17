# soubor: modules/notifier.py
# Opravy:
# - L6: Používá singleton `db` z core.database místo nové instance Database()
# - L6: Konfig z core.config.settings místo přímého os.getenv
# - B7+S5: X API implementováno s OAuth 1.0a (authlib) místo Bearer tokenu
import asyncio
import hashlib
import hmac
import logging
import time
import urllib.parse
import base64
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx

from core.database import db  # L6: singleton DB, ne nová instance
from core.config import settings  # L6: konfigurace ze settings
from core.event_bus import bus

logger = logging.getLogger("Predator.Notifier")


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
            mint = payload.get("mint") or payload.get("token_address")
            symbol = payload.get("symbol", "?")
            score = float(payload.get("score", 0))
            current_mcap = float(payload.get("market_cap", 0) or 0)
            current_price = float(payload.get("price", 0) or 0)

            growth_pct = min(10.0, max(0.05, score / 20.0))
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
            pid = db.save_prediction(record)

            followup_sec = settings.NOTIFIER_FOLLOWUP_SEC
            message = (
                f"Signal: {symbol} ({mint})\n"
                f"Score: {score:.1f} | Now MC: {current_mcap:.0f} | Now price: {current_price:.6f}\n"
                f"Prediction (in {followup_sec // 60}m): MC -> {predicted_mcap:.0f}, price -> {predicted_price:.6f}\n"
                f"Signal id: {pid}"
            )

            await self._send_broadcast(message)

            task = asyncio.create_task(
                self._schedule_followup(pid, mint, symbol, predicted_mcap, predicted_price)
            )
            self._tasks.add(task)
            task.add_done_callback(lambda t: self._tasks.discard(t))

        except Exception as e:
            logger.exception(f"Notifier failed handling trade signal: {e}")

    async def _on_risk_approved(self, payload: Dict[str, Any]):
        symbol = payload.get("symbol", "?")
        mint = payload.get("token_address") or payload.get("mint")
        message = f"RISK OK: Execution approved for {symbol} ({mint})"
        await self._send_broadcast(message)

    async def _on_position_closed(self, payload: Dict[str, Any]):
        address = payload.get("token_address", "?")
        profit_pct = payload.get("profit_pct", 0)
        reason = payload.get("reason", "?")
        message = f"Position closed: {address} | PnL: {profit_pct:.2f}% | Reason: {reason}"
        await self._send_broadcast(message)

    async def _on_veto(self, payload: Dict[str, Any]):
        address = payload.get("token_address", "?")
        reason = payload.get("reason", "vetoed by risk")
        message = f"VETO: {address} | {reason}"
        await self._send_broadcast(message)

    async def _schedule_followup(
        self,
        prediction_id: int,
        mint: str,
        symbol: str,
        predicted_mcap: float,
        predicted_price: float,
    ):
        try:
            await asyncio.sleep(settings.NOTIFIER_FOLLOWUP_SEC)

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
                            market_cap = pair.get("marketCap") or None
                            if market_cap:
                                actual_mcap = float(market_cap)
            except Exception:
                logger.exception("Failed to fetch market data for follow-up")

            if actual_mcap is None and actual_price is None:
                result_text = "no-data"
            else:
                ok = False
                if actual_mcap and predicted_mcap:
                    ok = actual_mcap >= predicted_mcap * 0.9
                elif actual_price and predicted_price:
                    ok = actual_price >= predicted_price * 0.9
                result_text = "hit" if ok else "miss"

            db.update_prediction_result(
                prediction_id,
                {
                    "actual_mcap": actual_mcap,
                    "actual_price": actual_price,
                    "result": result_text,
                },
            )

            follow_msg = (
                f"Follow-up [{prediction_id}]: {symbol} ({mint})\n"
                f"Predicted MC: {predicted_mcap:.0f} | Predicted price: {predicted_price:.6f}\n"
                f"Actual MC: {actual_mcap if actual_mcap is not None else 'n/a'} | "
                f"Actual price: {actual_price if actual_price is not None else 'n/a'}\n"
                f"Result: {result_text}"
            )
            await self._send_broadcast(follow_msg)

        except asyncio.CancelledError:
            logger.info(f"Follow-up task for prediction {prediction_id} cancelled")
        except Exception:
            logger.exception("Error in follow-up task")

    async def _send_broadcast(self, message: str):
        logger.info(f"Notifier broadcast:\n{message}")

        # Telegram
        if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
            await self._send_telegram(message)
        else:
            logger.debug("Telegram not configured (dry-run)")

        # B7+S5: X (Twitter) - OAuth 1.0a
        if (
            settings.X_API_KEY
            and settings.X_API_SECRET
            and settings.X_ACCESS_TOKEN
            and settings.X_ACCESS_TOKEN_SECRET
        ):
            await self._send_x_oauth(message)
        else:
            logger.debug("X (Twitter) not configured (dry-run)")

    async def _send_telegram(self, message: str):
        try:
            api = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    api,
                    json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": message},
                )
        except Exception:
            logger.exception("Failed sending Telegram message")

    # B7+S5: OAuth 1.0a implementace pro X (Twitter) POST /2/tweets
    async def _send_x_oauth(self, message: str):
        """Post tweet pomocí OAuth 1.0a (manuální HMAC-SHA1 signing)."""
        try:
            url = "https://api.twitter.com/2/tweets"
            method = "POST"

            # Truncate na 280 znaků (Twitter limit)
            tweet_text = message[:280]

            # OAuth 1.0a parametry
            oauth_params = {
                "oauth_consumer_key": settings.X_API_KEY,
                "oauth_nonce": secrets.token_hex(16),
                "oauth_signature_method": "HMAC-SHA1",
                "oauth_timestamp": str(int(time.time())),
                "oauth_token": settings.X_ACCESS_TOKEN,
                "oauth_version": "1.0",
            }

            # Signature base string
            all_params = dict(oauth_params)
            param_string = "&".join(
                f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
                for k, v in sorted(all_params.items())
            )
            base_string = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(param_string, safe='')}"

            signing_key = (
                f"{urllib.parse.quote(settings.X_API_SECRET, safe='')}"
                f"&{urllib.parse.quote(settings.X_ACCESS_TOKEN_SECRET, safe='')}"
            )

            signature = base64.b64encode(
                hmac.new(
                    signing_key.encode("utf-8"),
                    base_string.encode("utf-8"),
                    hashlib.sha1,
                ).digest()
            ).decode("utf-8")

            oauth_params["oauth_signature"] = signature

            auth_header = "OAuth " + ", ".join(
                f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
                for k, v in sorted(oauth_params.items())
            )

            headers = {
                "Authorization": auth_header,
                "Content-Type": "application/json",
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json={"text": tweet_text}, headers=headers)
                if resp.status_code in (200, 201):
                    logger.info("X tweet posted successfully")
                else:
                    logger.warning(f"X API responded {resp.status_code}: {resp.text[:200]}")

        except Exception:
            logger.exception("Failed sending X message via OAuth 1.0a")


notifier = Notifier()
