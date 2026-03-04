# soubor: main.py
# Hlavni vstupni bod PREDATOR systemu
# v2.0: Health check, vylepseny module management

import asyncio
import json
import logging
import signal
import sys

from core.event_bus import bus
from core.logging import setup_logging
from core.system_state import state
from core.database import db
from core.strategy import strategy
from modules.hunter import Hunter
from modules.miner import Miner
from modules.analyzer import analyzer
from modules.risk_guard import risk_guard
from modules.executor import executor
from modules.learner import learner
from modules.notifier import notifier
from modules.health_check import health_check

logger = logging.getLogger("Predator.Main")

_shutdown_event = asyncio.Event()


async def main():
    setup_logging(level_console="INFO", level_file="DEBUG")
    logger.info("=" * 70)
    logger.info("THE PREDATOR – Trading Bot Solana | v2.0-autonomous")
    logger.info(f"Mod: {state.mode} | Stav: {state.status}")
    logger.info(f"Balanc: {state.balance_sol:.4f} SOL")
    logger.info(f"Strategie: {strategy.get().name}")
    s = strategy.get()
    logger.info(
        f"MoonBag: {'ON' if s.moon_bag_enabled else 'OFF'} | "
        f"DCA: {'ON' if s.dca_enabled else 'OFF'} | "
        f"Cooldown: {s.cooldown_seconds}s | "
        f"TX Sim: {'ON' if s.use_tx_simulation else 'OFF'}"
    )
    logger.info("=" * 70)

    miner = Miner()
    hunter = Hunter()

    loop = asyncio.get_running_loop()

    def _signal_handler():
        logger.info("Shutdown signal prijat – ukoncuji vsechny moduly...")
        _shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        logger.info("Spoustim vsechny moduly...")

        modules = {
            "miner": miner,
            "hunter": hunter,
            "notifier": notifier,
            "analyzer": analyzer,
            "risk_guard": risk_guard,
            "executor": executor,
            "learner": learner,
            "health_check": health_check,
        }

        module_tasks = {}

        def start_module(name: str):
            mod = modules.get(name)
            if not mod:
                return None
            if getattr(mod, "is_running", False):
                logger.debug(f"Module {name} already running")
                return None
            t = asyncio.create_task(mod.start())
            module_tasks[name] = t
            logger.info(f"Module start requested: {name}")
            return t

        def stop_module_sync(name: str):
            mod = modules.get(name)
            if not mod:
                return None
            if getattr(mod, "is_running", False):
                asyncio.create_task(mod.stop())
                logger.info(f"Module stop requested: {name}")

        # Start all modules
        for name in modules.keys():
            start_module(name)

        if state.mode in ["BETA", "LIVE"]:
            from modules.wallet_manager import wallet_manager

            wm_task = asyncio.create_task(wallet_manager.start())
            module_tasks["wallet_manager"] = wm_task
            logger.info("WalletManager aktivovan (Live mod)")
        else:
            logger.info("WalletManager deaktivovan (Shadow/Paper mod)")

        # Command watcher: poll DB for unprocessed commands
        async def command_watcher():
            while not _shutdown_event.is_set():
                try:
                    rows = db.fetch_unprocessed_commands()
                    for rid, action, payload in rows:
                        logger.info(f"Processing command {rid}: {action} {payload}")
                        try:
                            data = json.loads(payload) if payload else {}
                        except Exception:
                            data = {}

                        if action == "set_mode":
                            new_mode = data.get("mode")
                            if new_mode:
                                state.mode = new_mode
                                state.save()
                                await bus.emit("MODE_CHANGED", {"mode": new_mode})
                        elif action == "set_status":
                            new_status = data.get("status")
                            if new_status:
                                state.status = new_status
                                state.save()
                                await bus.emit(
                                    "STATUS_CHANGED", {"status": new_status}
                                )
                                if new_status == "PAUSED":
                                    for m in list(modules.keys()):
                                        stop_module_sync(m)
                                if new_status == "RUNNING":
                                    for m in list(modules.keys()):
                                        start_module(m)
                        elif action == "module_toggle":
                            mod = data.get("module")
                            enable = data.get("enable", True)
                            if mod and mod in modules:
                                if enable:
                                    start_module(mod)
                                else:
                                    stop_module_sync(mod)
                        elif action == "strategy_change":
                            strat_name = data.get("strategy")
                            strategy.set_strategy(strat_name)
                            await bus.emit(
                                "STRATEGY_CHANGE", {"strategy": strat_name}
                            )
                            db.log_event(
                                "System", "STRATEGY_CHANGE",
                                f"Strategie zmenena na: {strat_name}",
                            )
                        elif action == "system_cmd":
                            cmd = data.get("cmd")
                            if cmd == "stop_all":
                                for m in list(modules.keys()):
                                    stop_module_sync(m)
                                state.status = "PAUSED"
                                state.save()
                            elif cmd == "learner_apply":
                                suggestion = data.get("suggestion", {})
                                await bus.emit("LEARNER_APPLY_SUGGESTION", suggestion)
                        db.mark_command_processed(rid)
                except Exception as e:
                    logger.exception(f"Command watcher error: {e}")
                await asyncio.sleep(2)

        async def module_reporter():
            while not _shutdown_event.is_set():
                try:
                    for name, mod in modules.items():
                        metrics = {}
                        for attr in [
                            "processed_count",
                            "selected_count",
                            "trade_count",
                            "open_positions",
                            "mint_counter_last_heartbeat",
                            "emit_counter_last_heartbeat",
                            "is_running",
                            "feedback_count",
                            "consecutive_losses",
                            "adjustments_made",
                            "signals_emitted",
                            "vetoes",
                            "approvals",
                        ]:
                            if hasattr(mod, attr):
                                try:
                                    metrics[attr] = getattr(mod, attr)
                                except Exception:
                                    metrics[attr] = None
                        try:
                            if name == "miner":
                                recent = db.fetch_recent_coin_updates(limit=10)
                                metrics["recent_coin_updates"] = (
                                    len(recent) if recent is not None else 0
                                )
                            if name == "learner":
                                recent_ls = db.fetch_recent_learner_suggestions(
                                    limit=10
                                )
                                metrics["recent_learner_suggestions"] = (
                                    len(recent_ls) if recent_ls is not None else 0
                                )
                            if name == "executor":
                                metrics["moon_bags"] = len(
                                    getattr(mod, "moon_bags", {})
                                )
                                metrics["portfolio_drawdown"] = round(
                                    mod.get_portfolio_drawdown(), 2
                                ) if hasattr(mod, "get_portfolio_drawdown") else 0
                        except Exception as e:
                            logger.debug(
                                f"Failed fetching recent DB summaries for reporter: {e}"
                            )

                        status = (
                            "running"
                            if getattr(mod, "is_running", False)
                            else "stopped"
                        )
                        db.save_module_report(name, status, json.dumps(metrics))
                except Exception:
                    logger.exception("Module reporter error")
                await asyncio.sleep(10)

        watcher_task = asyncio.create_task(command_watcher())
        reporter_task = asyncio.create_task(module_reporter())

        await _shutdown_event.wait()

    except KeyboardInterrupt:
        logger.info("Shutdown signal (Ctrl+C) – ukoncuji vsechny moduly...")
    except Exception as e:
        logger.critical(f"Kriticka chyba v hlavnim loopu: {e}", exc_info=True)
    finally:
        logger.info("Vypinam moduly...")
        shutdown_tasks = [
            miner.stop(),
            hunter.stop(),
            notifier.stop(),
            analyzer.stop(),
            risk_guard.stop(),
            executor.stop(),
            learner.stop(),
            health_check.stop(),
        ]

        if state.mode in ["BETA", "LIVE"]:
            from modules.wallet_manager import wallet_manager

            shutdown_tasks.append(wallet_manager.stop())

        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
        logger.info("PREDATOR shutdown dokoncen.")
        logger.info("=" * 70)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
