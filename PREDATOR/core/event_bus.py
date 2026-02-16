"""
core/event_bus.py – Centrální event bus (pub/sub) pro komunikaci mezi moduly.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("Predator.EventBus")


class EventBus:
    """Jednoduchý async pub/sub event bus."""

    def __init__(self) -> None:
        self._listeners: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Zaregistruje posluchače pro daný typ události."""
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)
        logger.debug(f"Listener registrovan: {event_type} -> {callback.__qualname__}")

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """Odregistruje posluchače."""
        if event_type in self._listeners:
            self._listeners[event_type] = [
                cb for cb in self._listeners[event_type] if cb != callback
            ]

    async def emit(self, event_type: str, payload: Any = None) -> None:
        """Vyšle událost všem registrovaným posluchačům."""
        if event_type not in self._listeners:
            return

        tasks = []
        for callback in self._listeners[event_type]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    tasks.append(callback(payload))
                else:
                    callback(payload)
            except Exception as e:
                logger.error(
                    f"Chyba v sync callbacku {callback.__qualname__} "
                    f"pro {event_type}: {e}",
                    exc_info=True,
                )

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        f"Chyba v async callbacku pro {event_type}: {result}",
                        exc_info=True,
                    )

        logger.debug(f"Event emitovan: {event_type}")

    def clear(self) -> None:
        """Vymaže všechny listenery (pro testy)."""
        self._listeners.clear()


# Singleton
bus = EventBus()
