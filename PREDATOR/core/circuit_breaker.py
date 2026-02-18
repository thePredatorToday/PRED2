# soubor: core/circuit_breaker.py
# Circuit breaker pro RPC a externi API volani

import logging
import time
from typing import Optional

logger = logging.getLogger("Predator.CircuitBreaker")


class CircuitBreaker:
    """
    Circuit breaker pattern.
    Stavy: CLOSED (normalni) -> OPEN (blokuje) -> HALF_OPEN (zkusi 1 volani)

    Pouziti:
        cb = CircuitBreaker("solana_rpc", failure_threshold=5, recovery_timeout=60)
        if cb.can_execute():
            try:
                result = await rpc_call()
                cb.record_success()
            except Exception:
                cb.record_failure()
        else:
            logger.warning("Circuit breaker OPEN - RPC nedostupny")
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self.state = self.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: float = 0.0
        self.half_open_calls = 0

    def can_execute(self) -> bool:
        """Vraci True pokud je povoleno volani."""
        if self.state == self.CLOSED:
            return True

        if self.state == self.OPEN:
            # Zkontrolujeme zda uplynul recovery timeout
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = self.HALF_OPEN
                self.half_open_calls = 0
                logger.info(f"CircuitBreaker [{self.name}]: OPEN -> HALF_OPEN")
                return True
            return False

        if self.state == self.HALF_OPEN:
            return self.half_open_calls < self.half_open_max_calls

        return False

    def record_success(self):
        """Zaznamena uspesne volani."""
        if self.state == self.HALF_OPEN:
            self.success_count += 1
            self.state = self.CLOSED
            self.failure_count = 0
            logger.info(f"CircuitBreaker [{self.name}]: HALF_OPEN -> CLOSED (recovered)")
        elif self.state == self.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self):
        """Zaznamena neuspesne volani."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == self.HALF_OPEN:
            self.state = self.OPEN
            logger.warning(f"CircuitBreaker [{self.name}]: HALF_OPEN -> OPEN (still failing)")
        elif self.state == self.CLOSED and self.failure_count >= self.failure_threshold:
            self.state = self.OPEN
            logger.warning(
                f"CircuitBreaker [{self.name}]: CLOSED -> OPEN "
                f"(failures: {self.failure_count}/{self.failure_threshold})"
            )

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "failure_count": self.failure_count,
            "last_failure": self.last_failure_time,
        }
