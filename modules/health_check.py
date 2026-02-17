# soubor: modules/health_check.py
# v2.0: HTTP /health endpoint pro monitoring (K8s, Docker, Prometheus)

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict

from aiohttp import web

from core.config import settings
from core.system_state import state
from core.event_bus import bus
from core.database import db

logger = logging.getLogger("Predator.HealthCheck")


class HealthCheck:
    """HTTP health check server pro monitoring a diagnostiku."""

    def __init__(self):
        self.is_running = False
        self.start_time = 0.0
        self.app = None
        self.runner = None
        self.port = settings.HEALTH_CHECK_PORT

    async def start(self):
        if not settings.HEALTH_CHECK_ENABLED:
            logger.info("Health check deaktivovan v konfiguraci")
            return

        self.is_running = True
        self.start_time = time.time()

        self.app = web.Application()
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_get("/ready", self._handle_ready)
        self.app.router.add_get("/metrics", self._handle_metrics)
        self.app.router.add_get("/status", self._handle_status)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await site.start()

        logger.info(f"HEALTH CHECK START - http://0.0.0.0:{self.port}/health")
        db.log_event("HealthCheck", "MODULE_START", f"Health check na portu {self.port}")

    async def stop(self):
        self.is_running = False
        if self.runner:
            await self.runner.cleanup()
        logger.info("HealthCheck zastaven")

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Liveness probe - je bot nazivu?"""
        uptime = time.time() - self.start_time
        healthy = state.status not in ("ERROR",)

        body = {
            "status": "healthy" if healthy else "unhealthy",
            "uptime_seconds": round(uptime, 1),
            "mode": state.mode,
            "system_status": state.status,
            "timestamp": datetime.utcnow().isoformat(),
        }

        status_code = 200 if healthy else 503
        return web.json_response(body, status=status_code)

    async def _handle_ready(self, request: web.Request) -> web.Response:
        """Readiness probe - je bot pripraven obchodovat?"""
        ready = state.status == "RUNNING" and state.balance_sol > state.solana_reserve

        body = {
            "ready": ready,
            "status": state.status,
            "balance_sol": round(state.balance_sol, 4),
            "mode": state.mode,
        }

        status_code = 200 if ready else 503
        return web.json_response(body, status=status_code)

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """Prometheus-style metriky."""
        try:
            from modules.executor import executor
            from modules.learner import learner

            metrics = self._collect_metrics(executor, learner)
            return web.json_response(metrics)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Detailni status celeho systemu."""
        try:
            from modules.executor import executor
            from modules.learner import learner
            from core.strategy import strategy

            s = strategy.get()
            uptime = time.time() - self.start_time

            learner_metrics = learner.metrics.get_metrics()

            status = {
                "system": {
                    "mode": state.mode,
                    "status": state.status,
                    "uptime_seconds": round(uptime, 1),
                    "balance_sol": round(state.balance_sol, 4),
                    "daily_pnl_sol": round(state.daily_pnl, 4),
                    "open_positions": state.open_positions,
                },
                "strategy": {
                    "name": s.name,
                    "moon_bag": s.moon_bag_enabled,
                    "dca": s.dca_enabled,
                    "cooldown": s.cooldown_seconds,
                    "slippage_bps": s.slippage_bps,
                },
                "executor": {
                    "active_positions": len(executor.active_positions),
                    "moon_bags": len(executor.moon_bags),
                    "total_trades": executor.trade_count,
                    "portfolio_drawdown": round(executor.get_portfolio_drawdown(), 2),
                    "total_pnl_pct": round(executor.get_total_pnl(), 2),
                },
                "learner": {
                    "total_trades": learner_metrics.get("total_trades", 0),
                    "win_rate": round(learner_metrics.get("win_rate", 0), 3),
                    "profit_factor": round(learner_metrics.get("profit_factor", 0), 2),
                    "adjustments": learner.adjustments_made,
                    "auto_apply": learner.auto_apply,
                },
                "event_bus": bus.get_metrics(),
            }

            return web.json_response(status)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    def _collect_metrics(self, executor, learner) -> Dict[str, Any]:
        """Sbira metriky pro Prometheus."""
        uptime = time.time() - self.start_time
        learner_metrics = learner.metrics.get_metrics()

        return {
            "predator_uptime_seconds": round(uptime, 1),
            "predator_balance_sol": round(state.balance_sol, 4),
            "predator_daily_pnl_sol": round(state.daily_pnl, 4),
            "predator_open_positions": state.open_positions,
            "predator_total_trades": executor.trade_count,
            "predator_moon_bags": len(executor.moon_bags),
            "predator_portfolio_drawdown_pct": round(executor.get_portfolio_drawdown(), 2),
            "predator_total_pnl_pct": round(executor.get_total_pnl(), 2),
            "predator_win_rate": round(learner_metrics.get("win_rate", 0), 3),
            "predator_profit_factor": round(learner_metrics.get("profit_factor", 0), 2),
            "predator_learner_adjustments": learner.adjustments_made,
            "predator_event_bus_emitted": bus.total_emitted,
            "predator_event_bus_dropped": bus.total_dropped,
            "predator_event_bus_errors": bus.total_errors,
            "predator_mode": state.mode,
            "predator_status": state.status,
            "predator_strategy": executor.__class__.__name__,
        }


health_check = HealthCheck()
