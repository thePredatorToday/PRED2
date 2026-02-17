# soubor: modules/learner.py
# v2.0: Persistentni metriky, auto-apply parametru,
#       adaptivni strategie na zaklade vykonnosti

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.event_bus import bus
from core.database import db
from core.config import settings
from core.strategy import strategy

logger = logging.getLogger("Predator.Learner")


class TradeMetrics:
    """Persistentni metriky z uzavrenych obchodu."""

    def __init__(self):
        self.trades: List[Dict[str, Any]] = []
        self.win_count = 0
        self.loss_count = 0
        self.total_pnl = 0.0
        self.total_pnl_sol = 0.0
        self.best_trade_pct = 0.0
        self.worst_trade_pct = 0.0
        self.last_update = datetime.utcnow()
        self.exit_reasons: Dict[str, int] = {}

    def add_trade(self, trade_data: Dict[str, Any]):
        profit = trade_data.get("profit_pct", 0)
        profit_sol = trade_data.get("profit_sol", 0)
        reason = trade_data.get("reason", "UNKNOWN")

        self.trades.append(trade_data)
        self.total_pnl += profit
        self.total_pnl_sol += profit_sol

        if profit > 0:
            self.win_count += 1
        else:
            self.loss_count += 1

        if profit > self.best_trade_pct:
            self.best_trade_pct = profit
        if profit < self.worst_trade_pct:
            self.worst_trade_pct = profit

        self.exit_reasons[reason] = self.exit_reasons.get(reason, 0) + 1
        self.last_update = datetime.utcnow()

    def get_metrics(self) -> Dict[str, Any]:
        total = self.win_count + self.loss_count
        if total == 0:
            return {}

        win_rate = self.win_count / total

        total_wins = sum(t.get("profit_pct", 0) for t in self.trades if t.get("profit_pct", 0) > 0)
        total_losses = abs(sum(t.get("profit_pct", 0) for t in self.trades if t.get("profit_pct", 0) < 0))
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

        return {
            "total_trades": total,
            "win_rate": win_rate,
            "loss_rate": self.loss_count / total,
            "total_pnl_pct": self.total_pnl,
            "total_pnl_sol": self.total_pnl_sol,
            "avg_profit": self.total_pnl / total,
            "best_trade": self.best_trade_pct,
            "worst_trade": self.worst_trade_pct,
            "profit_factor": profit_factor,
            "exit_reasons": self.exit_reasons,
            "last_trade": self.trades[-1].get("timestamp") if self.trades else None,
        }

    def get_recent_metrics(self, n: int = 10) -> Dict[str, Any]:
        recent = self.trades[-n:]
        if not recent:
            return {}
        wins = sum(1 for t in recent if t.get("profit_pct", 0) > 0)
        total = len(recent)
        avg_pnl = sum(t.get("profit_pct", 0) for t in recent) / total
        return {
            "recent_trades": total,
            "recent_win_rate": wins / total,
            "recent_avg_pnl": avg_pnl,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "total_pnl": self.total_pnl,
            "total_pnl_sol": self.total_pnl_sol,
            "best_trade_pct": self.best_trade_pct,
            "worst_trade_pct": self.worst_trade_pct,
            "exit_reasons": self.exit_reasons,
            "trades_count": len(self.trades),
        }


class Learner:
    """
    Adaptivni uceni s persistenci a auto-apply.
    Sleduje vykon a automaticky upravuje parametry strategie.
    """

    def __init__(self):
        self.is_running = False
        self.metrics = TradeMetrics()
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        self.feedback_count = 0
        self.adjustments_made = 0
        self.auto_apply = settings.LEARNER_AUTO_APPLY
        self.min_trades_for_adjust = settings.LEARNER_MIN_TRADES_FOR_ADJUST

    async def start(self):
        self.is_running = True
        logger.info("LEARNER START - Zpetna vazba a adaptivni uceni aktivovany")

        self._load_metrics_from_db()

        bus.subscribe("POSITION_CLOSED", self.on_position_closed)
        bus.subscribe("LEARNER_APPLY_SUGGESTION", self._on_manual_apply)
        asyncio.create_task(self._heartbeat())
        asyncio.create_task(self._periodic_analysis())

        db.log_event("Learner", "MODULE_START",
                      f"Learner spusten | Auto-apply: {'ON' if self.auto_apply else 'OFF'}")

    async def stop(self):
        self.is_running = False
        self._save_metrics_to_db()
        logger.info("Learner zastaven")

    async def _heartbeat(self):
        while self.is_running:
            await asyncio.sleep(300)
            metrics = self.metrics.get_metrics()
            if metrics:
                logger.info(
                    f"Learner | Obchody: {metrics.get('total_trades', 0)} | "
                    f"Win Rate: {metrics.get('win_rate', 0):.1%} | "
                    f"PnL: {metrics.get('total_pnl_pct', 0):.2f}% | "
                    f"Profit Factor: {metrics.get('profit_factor', 0):.2f} | "
                    f"Adjustments: {self.adjustments_made}"
                )

    async def _periodic_analysis(self):
        while self.is_running:
            await asyncio.sleep(1800)
            await self._deep_analysis()

    def _load_metrics_from_db(self):
        try:
            conn = db.connect()
            cursor = conn.execute(
                "SELECT value FROM system_state WHERE key = 'learner_metrics'"
            )
            row = cursor.fetchone()
            if row:
                data = json.loads(row[0])
                self.metrics.win_count = data.get("win_count", 0)
                self.metrics.loss_count = data.get("loss_count", 0)
                self.metrics.total_pnl = data.get("total_pnl", 0)
                self.metrics.total_pnl_sol = data.get("total_pnl_sol", 0)
                self.metrics.best_trade_pct = data.get("best_trade_pct", 0)
                self.metrics.worst_trade_pct = data.get("worst_trade_pct", 0)
                self.metrics.exit_reasons = data.get("exit_reasons", {})
                self.feedback_count = data.get("trades_count", 0)
                logger.info(f"Learner: Nacteno {self.feedback_count} historickych obchodu z DB")
        except Exception as e:
            logger.debug(f"Learner: Nelze nacist metriky z DB: {e}")

    def _save_metrics_to_db(self):
        try:
            conn = db.connect()
            data = json.dumps(self.metrics.to_dict())
            conn.execute(
                "INSERT OR REPLACE INTO system_state (key, value) VALUES ('learner_metrics', ?)",
                (data,),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Learner: Chyba pri ukladani metrik: {e}")

    async def on_position_closed(self, event_data: Dict[str, Any]):
        self.feedback_count += 1
        profit_pct = event_data.get("profit_pct", 0)
        reason = event_data.get("reason", "UNKNOWN")
        address = event_data.get("token_address", "?")
        trade_type = event_data.get("type", "full")

        self.metrics.add_trade({
            "token_address": address,
            "profit_pct": profit_pct,
            "profit_sol": event_data.get("profit_sol", 0),
            "reason": reason,
            "type": trade_type,
            "timestamp": datetime.utcnow().isoformat(),
        })

        logger.info(
            f"Learner: Obchod uzavren: {address} | Zisk: {profit_pct:.2f}% | "
            f"Duvod: {reason} | Typ: {trade_type}"
        )

        if profit_pct < 0:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            if self.consecutive_losses >= 3:
                await self._on_loss_streak()
        else:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            if self.consecutive_wins >= 5:
                await self._on_win_streak()

        if self.feedback_count % 10 == 0:
            await self._generate_feedback_report()
            if self.auto_apply and self.feedback_count >= self.min_trades_for_adjust:
                await self._auto_adjust_parameters()

        if self.feedback_count % 5 == 0:
            self._save_metrics_to_db()

    async def _on_loss_streak(self):
        metrics = self.metrics.get_metrics()
        if not metrics:
            return

        win_rate = metrics.get("win_rate", 0)
        logger.warning(
            f"Learner: VAROVANI - {self.consecutive_losses} ztrat v rade! "
            f"Win rate: {win_rate:.1%}"
        )

        suggestion = None
        if win_rate < 0.25:
            suggestion = {
                "action": "SWITCH_CONSERVATIVE",
                "reason": f"Win rate {win_rate:.1%} < 25% + {self.consecutive_losses} ztrat",
            }
        elif win_rate < 0.40:
            suggestion = {
                "action": "INCREASE_MIN_SCORE",
                "reason": f"Win rate {win_rate:.1%} < 40%",
            }

        if suggestion:
            if self.auto_apply:
                await self._apply_suggestion(suggestion)
            else:
                await bus.emit("LEARNER_SUGGESTION", suggestion)
                db.save_learner_suggestion({
                    "mint": None,
                    "suggestion": suggestion["action"],
                    "score": win_rate,
                })

    async def _on_win_streak(self):
        metrics = self.metrics.get_metrics()
        if not metrics:
            return

        win_rate = metrics.get("win_rate", 0)
        profit_factor = metrics.get("profit_factor", 0)

        if win_rate > 0.65 and profit_factor > 2.0:
            s = strategy.get()
            if s.name == "conservative":
                suggestion = {
                    "action": "SWITCH_DEFAULT",
                    "reason": f"Win rate {win_rate:.1%} + PF {profit_factor:.2f}",
                }
                if self.auto_apply:
                    await self._apply_suggestion(suggestion)

    async def _auto_adjust_parameters(self):
        metrics = self.metrics.get_metrics()
        recent = self.metrics.get_recent_metrics(10)
        if not metrics or not recent:
            return

        win_rate = metrics.get("win_rate", 0.5)
        recent_win_rate = recent.get("recent_win_rate", 0.5)
        profit_factor = metrics.get("profit_factor", 1.0)
        exit_reasons = metrics.get("exit_reasons", {})
        total = metrics.get("total_trades", 1)

        adjustments = []

        sl_count = exit_reasons.get("STOP_LOSS", 0)
        sl_rate = sl_count / total
        if sl_rate > 0.4:
            adjustments.append({
                "action": "WIDEN_STOP_LOSS",
                "reason": f"SL rate {sl_rate:.1%} > 40%",
            })

        if recent_win_rate < 0.25 and win_rate < 0.35:
            adjustments.append({
                "action": "SWITCH_CONSERVATIVE",
                "reason": f"Recent WR {recent_win_rate:.1%}, Overall WR {win_rate:.1%}",
            })
        elif recent_win_rate > 0.70 and profit_factor > 2.5:
            adjustments.append({
                "action": "CONSIDER_AGGRESSIVE",
                "reason": f"Recent WR {recent_win_rate:.1%}, PF {profit_factor:.2f}",
            })

        for adj in adjustments:
            await self._apply_suggestion(adj)

    async def _apply_suggestion(self, suggestion: Dict[str, Any]):
        action = suggestion.get("action", "")
        reason = suggestion.get("reason", "")

        if action == "SWITCH_CONSERVATIVE":
            current = strategy.get().name
            if current != "conservative":
                strategy.set_strategy("conservative")
                await bus.emit("STRATEGY_CHANGE", {"strategy": "conservative"})
                self.adjustments_made += 1
                logger.warning(f"Learner AUTO-APPLY: -> CONSERVATIVE | {reason}")
                db.log_event("Learner", "AUTO_APPLY",
                             f"Strategie -> conservative | {reason}", level="WARNING")

        elif action == "SWITCH_DEFAULT":
            current = strategy.get().name
            if current != "default":
                strategy.set_strategy("default")
                await bus.emit("STRATEGY_CHANGE", {"strategy": "default"})
                self.adjustments_made += 1
                logger.info(f"Learner AUTO-APPLY: -> DEFAULT | {reason}")
                db.log_event("Learner", "AUTO_APPLY", f"Strategie -> default | {reason}")

        elif action in ("CONSIDER_AGGRESSIVE", "INCREASE_MIN_SCORE",
                         "WIDEN_STOP_LOSS", "LOWER_TRAILING_DROP"):
            logger.info(f"Learner NAVRH: {action} | {reason}")
            db.log_event("Learner", "SUGGESTION", f"Navrh: {action} | {reason}")

        try:
            db.save_learner_suggestion({
                "mint": None,
                "suggestion": json.dumps(suggestion),
                "score": self.metrics.get_metrics().get("win_rate", 0),
            })
        except Exception:
            pass

    async def _on_manual_apply(self, payload: Dict[str, Any]):
        await self._apply_suggestion(payload)

    async def _deep_analysis(self):
        metrics = self.metrics.get_metrics()
        if not metrics or metrics.get("total_trades", 0) < 5:
            return

        win_rate = metrics.get("win_rate", 0)
        pf = metrics.get("profit_factor", 0)

        analysis = {
            "timestamp": datetime.utcnow().isoformat(),
            "total_trades": metrics["total_trades"],
            "win_rate": f"{win_rate:.1%}",
            "profit_factor": f"{pf:.2f}",
            "total_pnl": f"{metrics['total_pnl_pct']:.2f}%",
            "strategy": strategy.get().name,
            "adjustments": self.adjustments_made,
        }

        db.log_event(
            "Learner", "DEEP_ANALYSIS",
            f"Analyza: WR={win_rate:.1%} PF={pf:.2f} PnL={metrics['total_pnl_pct']:.2f}%",
            details=json.dumps(analysis),
        )

    async def _generate_feedback_report(self):
        metrics = self.metrics.get_metrics()
        if not metrics:
            return

        logger.info("=" * 60)
        logger.info("LEARNER REPORT - 10 TRADE SUMMARY")
        logger.info(f"Total Trades: {metrics.get('total_trades', 0)}")
        logger.info(f"Win Rate: {metrics.get('win_rate', 0):.1%}")
        logger.info(f"Profit Factor: {metrics.get('profit_factor', 0):.2f}")
        logger.info(f"Avg Profit: {metrics.get('avg_profit', 0):.2f}%")
        logger.info(f"Total PnL: {metrics.get('total_pnl_pct', 0):.2f}%")
        logger.info(f"Best: {metrics.get('best_trade', 0):.2f}% | Worst: {metrics.get('worst_trade', 0):.2f}%")
        logger.info(f"Exit Reasons: {metrics.get('exit_reasons', {})}")
        logger.info(f"Auto-adjustments: {self.adjustments_made}")
        logger.info("=" * 60)


# Singleton instance
learner = Learner()
