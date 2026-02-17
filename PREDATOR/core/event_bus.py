"""
core/event_bus.py – Centrální event bus (pub/sub) pro komunikaci mezi moduly.

Opravy vs. originál:
- M2: Deduplikace subscriberů (stejný callback se nepřidá 2x)
- Lepší error izolace a logging
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Set

logger = logging.getLogger("Predator.EventBus")


class EventBus:
    """Async pub/sub event bus s deduplikací."""

    def __init__(self) -> None:
        self._listeners: Dict[str, List[Callable]] = {}
        self._registered: Set[tuple] = set()

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Zaregistruje posluchače. Stejný callback pro stejný event se nepřidá 2x."""
        key = (event_type, id(callback))
        if key in self._registered:
            logger.debug(
                f"Listener již registrován (skip): {event_type} -> {callback.__qualname__}"
            )
            return

        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)
        self._registered.add(key)
        logger.debug(f"Listener registrován: {event_type} -> {callback.__qualname__}")

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """Odregistruje posluchače."""
        key = (event_type, id(callback))
        self._registered.discard(key)
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

        logger.debug(f"Event emitován: {event_type} ({len(tasks)} async listeners)")

    def clear(self) -> None:
        """Vymaže všechny listenery (pro testy)."""
        self._listeners.clear()
        self._registered.clear()


# Singleton
bus = EventBus()
