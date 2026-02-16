# soubor: modules/learner.py
# Learner: Zpětná vazba a adaptivní učení
# Vstupy: POSITION_CLOSED events od Executora
# Výstupy: Návrhy na ajustaci parametrů (manuálně schváleny)

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.event_bus import bus
from core.database import db
from core.config import settings

logger = logging.getLogger("Predator.Learner")


class TradeMetrics:
    """Metriky z uzavřených obchodů."""
    def __init__(self):
        self.trades: List[Dict[str, Any]] = []
        self.win_count = 0
        self.loss_count = 0
        self.total_pnl = 0.0
        self.last_update = datetime.utcnow()

    def add_trade(self, trade_data: Dict[str, Any]):
        """Přidá uzavřený obchod."""
        profit = trade_data.get("profit_pct", 0)
        self.trades.append(trade_data)
        self.total_pnl += profit

        if profit > 0:
            self.win_count += 1
        else:
            self.loss_count += 1

        self.last_update = datetime.utcnow()

    def get_metrics(self) -> Dict[str, Any]:
        """Vrátí souhrnné metriky."""
        total = self.win_count + self.loss_count
        if total == 0:
            return {}

        return {
            "total_trades": total,
            "win_rate": self.win_count / total,
            "loss_rate": self.loss_count / total,
            "total_pnl_pct": self.total_pnl,
            "avg_profit": self.total_pnl / total if total > 0 else 0,
            "last_trade": self.trades[-1]["timestamp"] if self.trades else None,
        }


class Learner:
    """
    Uhlazovací modul pro adaptivní parametry.
    Sleduje výkon a navrhuje změny v parametrech (manuálně schváleno).
    """

    def __init__(self):
        self.is_running = False
        self.metrics = TradeMetrics()
        self.consecutive_losses = 0
        self.feedback_count = 0

    async def start(self):
        """Spustí Learner – poslouchá POSITION_CLOSED."""
        self.is_running = True
        logger.info("🧠 LEARNER START – Zpětná vazba a učení aktivovány")

        # Poslouchá uzavřené pozice od Executora
        bus.subscribe("POSITION_CLOSED", self.on_position_closed)
        asyncio.create_task(self._heartbeat())

    async def stop(self):
        """Zastaví Learner."""
        self.is_running = False
        logger.info("🛑 Learner zastaven")

    async def _heartbeat(self):
        """Periodické hlášení metriky."""
        while self.is_running:
            await asyncio.sleep(300)  # 5 minut
            metrics = self.metrics.get_metrics()
            if metrics:
                logger.info(
                    f"🧠 Learner aktivní | Obchody: {metrics.get('total_trades', 0)} | "
                    f"Win Rate: {metrics.get('win_rate', 0):.1%} | "
                    f"Total PnL: {metrics.get('total_pnl_pct', 0):.2f}%"
                )

    async def on_position_closed(self, event_data: Dict[str, Any]):
        """Přijme uzavřenou pozici a analyzuje ji."""
        self.feedback_count += 1

        profit_pct = event_data.get("profit_pct", 0)
        reason = event_data.get("reason", "UNKNOWN")
        address = event_data.get("token_address", "?")

        # Zaznamenáme metrika
        self.metrics.add_trade(
            {
                "token_address": address,
                "profit_pct": profit_pct,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        logger.info(
            f"🧠 Obchod uzavřen: {address} | Zisk: {profit_pct:.2f}% | Důvod: {reason}"
        )

        # Sledujeme ztráty – pokud 3+ po sobě, signalizujeme varování
        if profit_pct < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= 3:
                await self._suggest_parameter_adjustment()
        else:
            self.consecutive_losses = 0

        # Každých 10 obchodů vyhodnotíme trend
        if self.feedback_count % 10 == 0:
            await self._generate_feedback_report()

    async def _suggest_parameter_adjustment(self):
        """Navrhne změny parametrů pokud je viditelný trend."""
        metrics = self.metrics.get_metrics()
        if not metrics:
            return

        win_rate = metrics.get("win_rate", 0)
        logger.warning(f"🧠 VAROVÁNÍ: {self.consecutive_losses} po sobě jdoucích ztrát!")

        if win_rate < 0.3:
            logger.warning(
                "🧠 Navrhování: Zvýšit min_liquidity nebo stricter filtry v Hunter"
            )
            # V reálu by se tady emitoval event pro Dashboard/admin
            suggestion_payload = {
                "suggestion": "INCREASE_LIQUIDITY_THRESHOLD",
                "reason": f"Low win rate: {win_rate:.1%}",
                "timestamp": datetime.utcnow().isoformat(),
                "score": float(win_rate),
            }
            await bus.emit("LEARNER_SUGGESTION", suggestion_payload)
            try:
                db.save_learner_suggestion({
                    "mint": None,
                    "suggestion": suggestion_payload.get("suggestion"),
                    "score": suggestion_payload.get("score", 0.0),
                })
            except Exception as e:
                logger.error(f"Failed saving learner suggestion to DB: {e}")

    async def _generate_feedback_report(self):
        """Generuje detailní report po 10 obchodech."""
        metrics = self.metrics.get_metrics()
        if not metrics:
            return

        logger.info("=" * 60)
        logger.info("🧠 LEARNER REPORT - 10 TRADE SUMMARY")
        logger.info(f"Total Trades: {metrics.get('total_trades', 0)}")
        logger.info(f"Win Rate: {metrics.get('win_rate', 0):.1%}")
        logger.info(f"Avg Profit per Trade: {metrics.get('avg_profit', 0):.2f}%")
        logger.info(f"Total PnL: {metrics.get('total_pnl_pct', 0):.2f}%")
        logger.info("=" * 60)

        # V reálu by to bylo uloženo do analytické DB
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "metrics": metrics,
            "recent_trades": self.metrics.trades[-10:],
        }

        # Uloží report (v budoucnosti do DB)
        logger.debug(f"Report saved: {json.dumps(report, default=str, indent=2)}")


# Singleton instance
learner = Learner()
