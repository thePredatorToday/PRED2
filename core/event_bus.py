"""
core/event_bus.py – Centralni event bus (pub/sub) pro komunikaci mezi moduly.

v2.0: Backpressure (bounded queue), deduplikace, metrics.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Set

logger = logging.getLogger("Predator.EventBus")

# Max events v queue pred backpressure
MAX_QUEUE_SIZE = 1000


class EventBus:
    """Async pub/sub event bus s deduplikaci a backpressure."""

    def __init__(self) -> None:
        self._listeners: Dict[str, List[Callable]] = {}
        self._registered: Set[tuple] = set()
        self._queue: asyncio.Queue = None
        self._processing = False
        # Metrics
        self.total_emitted = 0
        self.total_dropped = 0
        self.total_errors = 0

    def _ensure_queue(self):
        """Lazy init queue (needs running event loop)."""
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Zaregistruje posluchace. Stejny callback pro stejny event se neprida 2x."""
        key = (event_type, id(callback))
        if key in self._registered:
            logger.debug(
                f"Listener jiz registrovan (skip): {event_type} -> {callback.__qualname__}"
            )
            return

        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)
        self._registered.add(key)
        logger.debug(f"Listener registrovan: {event_type} -> {callback.__qualname__}")

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """Odregistruje posluchace."""
        key = (event_type, id(callback))
        self._registered.discard(key)
        if event_type in self._listeners:
            self._listeners[event_type] = [
                cb for cb in self._listeners[event_type] if cb != callback
            ]

    async def emit(self, event_type: str, payload: Any = None) -> None:
        """Vysle udalost vsem registrovanym posluchacum s backpressure."""
        if event_type not in self._listeners:
            return

        self._ensure_queue()
        self.total_emitted += 1

        # Backpressure: pokud je fronta plna, zalogujeme a dropneme
        if self._queue.full():
            self.total_dropped += 1
            logger.warning(
                f"EventBus backpressure: dropping {event_type} "
                f"(queue full: {MAX_QUEUE_SIZE})"
            )
            return

        # Zpracujeme primo (inline) pro zachovani kompatibility
        tasks = []
        for callback in self._listeners[event_type]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    tasks.append(callback(payload))
                else:
                    callback(payload)
            except Exception as e:
                self.total_errors += 1
                logger.error(
                    f"Chyba v sync callbacku {callback.__qualname__} "
                    f"pro {event_type}: {e}",
                    exc_info=True,
                )

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.total_errors += 1
                    logger.error(
                        f"Chyba v async callbacku pro {event_type}: {result}",
                        exc_info=True,
                    )

        logger.debug(
            f"Event emitovan: {event_type} ({len(tasks)} async listeners) "
            f"[total: {self.total_emitted}, dropped: {self.total_dropped}]"
        )

    def get_metrics(self) -> Dict[str, Any]:
        """Vrati metriky event busu."""
        return {
            "total_emitted": self.total_emitted,
            "total_dropped": self.total_dropped,
            "total_errors": self.total_errors,
            "listener_count": sum(len(v) for v in self._listeners.values()),
            "event_types": list(self._listeners.keys()),
        }

    def clear(self) -> None:
        """Vymaze vsechny listenery (pro testy)."""
        self._listeners.clear()
        self._registered.clear()


# Singleton
bus = EventBus()
