# soubor: main.py
# Hlavní vstupní bod PREDATOR systému – spouští Miner + Hunter + event bus pipeline

import asyncio
import logging
import signal
import sys

from core.event_bus import bus
from core.logging import setup_logging
from core.system_state import state
from core.database import db
from modules.hunter import Hunter
from modules.miner import Miner
from modules.analyzer import analyzer
from modules.risk_guard import risk_guard
from modules.executor import executor
from modules.learner import learner
from modules.notifier import notifier
# wallet_manager - načteme jen v BETA/Live módu v main()

logger = logging.getLogger("Predator.Main")


async def main():
    # Inicializace logování (INFO v konzoli, DEBUG v souboru)
    setup_logging(level_console="INFO", level_file="DEBUG")
    logger.info("=" * 70)
    logger.info("🦅 THE PREDATOR – Trading Bot Solana | v1.3")
    logger.info(f"🚀 Mód: {state.mode} | Stav: {state.status}")
    logger.info(f"💼 Balanc: {state.balance_sol:.4f} SOL")
    logger.info("=" * 70)

    # Vytvoření modulů
    miner = Miner()
    hunter = Hunter()

    # Propojení pipeline přes event bus
    bus.subscribe("NEW_COIN_FOUND", hunter.evaluate)
    # Analyzer poslouchá Huntera a COIN_UPDATE pro historii (řešeno interně v analyzer.start())

    try:
        # Spuštění všech modulů paralelně
        logger.info("🚀 Spouštím všechny moduly...")

        # Start modules as background tasks and keep references so we can
        # control them at runtime via DB commands (dashboard -> commands table)
        modules = {
            'miner': miner,
            'hunter': hunter,
            'notifier': notifier,
            'analyzer': analyzer,
            'risk_guard': risk_guard,
            'executor': executor,
            'learner': learner,
        }

        module_tasks = {}

        # Helper to start a module and keep task
        def start_module(name: str):
            mod = modules.get(name)
            if not mod:
                return None
            if getattr(mod, 'is_running', False):
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
            if getattr(mod, 'is_running', False):
                # schedule stop
                asyncio.create_task(mod.stop())
                logger.info(f"Module stop requested: {name}")

        # Start modules according to mode (WalletManager handled separately)
        for name in modules.keys():
            start_module(name)

        if state.mode in ["BETA", "LIVE"]:
            from modules.wallet_manager import wallet_manager
            wm_task = asyncio.create_task(wallet_manager.start())
            module_tasks['wallet_manager'] = wm_task
            logger.info("💳 WalletManager aktivován (Live mód)")
        else:
            logger.info("💳 WalletManager deaktivován (Shadow/Paper mód)")

        # Command watcher: poll DB for unprocessed commands and execute them
        async def command_watcher():
            import json
            while True:
                try:
                    rows = db.fetch_unprocessed_commands()
                    for rid, action, payload in rows:
                        logger.info(f"Processing command {rid}: {action} {payload}")
                        try:
                            data = json.loads(payload) if payload else {}
                        except Exception:
                            data = {}

                        if action == 'set_mode':
                            new_mode = data.get('mode')
                            if new_mode:
                                state.mode = new_mode
                                state.save()
                                await bus.emit('MODE_CHANGED', {'mode': new_mode})
                        elif action == 'set_status':
                            new_status = data.get('status')
                            if new_status:
                                state.status = new_status
                                state.save()
                                await bus.emit('STATUS_CHANGED', {'status': new_status})
                                # For simple control, if PAUSED -> stop modules, RUNNING -> start modules
                                if new_status == 'PAUSED':
                                    for m in list(modules.keys()):
                                        stop_module_sync(m)
                                if new_status == 'RUNNING':
                                    for m in list(modules.keys()):
                                        start_module(m)
                        elif action == 'module_toggle':
                            mod = data.get('module')
                            enable = data.get('enable', True)
                            if mod and mod in modules:
                                if enable:
                                    start_module(mod)
                                else:
                                    stop_module_sync(mod)
                        elif action == 'strategy_change':
                            strategy = data.get('strategy')
                            await bus.emit('STRATEGY_CHANGE', {'strategy': strategy})
                        elif action == 'system_cmd':
                            cmd = data.get('cmd')
                            if cmd == 'stop_all':
                                for m in list(modules.keys()):
                                    stop_module_sync(m)
                                state.status = 'PAUSED'
                                state.save()
                        # mark processed
                        db.mark_command_processed(rid)
                except Exception as e:
                    logger.exception(f"Command watcher error: {e}")
                await asyncio.sleep(2)

        async def module_reporter():
            import json
            while True:
                try:
                    for name, mod in modules.items():
                        metrics = {}
                        # collect common attributes if present
                        for attr in ['processed_count','selected_count','trade_count','open_positions','mint_counter_last_heartbeat','emit_counter_last_heartbeat','is_running','feedback_count','consecutive_losses']:
                            if hasattr(mod, attr):
                                try:
                                    metrics[attr] = getattr(mod, attr)
                                except Exception:
                                    metrics[attr] = None
                        # enrich with recent counts for miner/learner
                        try:
                            if name == 'miner':
                                recent = db.fetch_recent_coin_updates(limit=10)
                                metrics['recent_coin_updates'] = len(recent) if recent is not None else 0
                            if name == 'learner':
                                recent_ls = db.fetch_recent_learner_suggestions(limit=10)
                                metrics['recent_learner_suggestions'] = len(recent_ls) if recent_ls is not None else 0
                        except Exception as e:
                            logger.debug(f"Failed fetching recent DB summaries for reporter: {e}")

                        status = 'running' if getattr(mod, 'is_running', False) else 'stopped'
                        db.save_module_report(name, status, json.dumps(metrics))
                except Exception:
                    logger.exception('Module reporter error')
                await asyncio.sleep(10)

        # Start watchers
        watcher_task = asyncio.create_task(command_watcher())
        reporter_task = asyncio.create_task(module_reporter())

        # Keep main alive
        await asyncio.gather(watcher_task, reporter_task)
    except KeyboardInterrupt:
        logger.info("⚠️  Shutdown signál (Ctrl+C) – ukončuji všechny moduly...")
    except Exception as e:
        logger.critical(f"❌ Kritická chyba v hlavním loopu: {e}", exc_info=True)
    finally:
        # Graceful shutdown – zavolá stop() na všech modulech
        logger.info("🛑 Vypínám moduly...")
        shutdown_tasks = [
            miner.stop(),
            hunter.stop(),
            notifier.stop(),
            analyzer.stop(),
            risk_guard.stop(),
            executor.stop(),
            learner.stop(),
        ]
        
        if state.mode in ["BETA", "LIVE"]:
            from modules.wallet_manager import wallet_manager
            shutdown_tasks.append(wallet_manager.stop())
        
        await asyncio.gather(*shutdown_tasks, return_exceptions=True)
        logger.info("🦅 PREDATOR shutdown dokončen. Do příště!")
        logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
