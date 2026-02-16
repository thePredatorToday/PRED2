import os
import asyncio
import logging
from datetime import datetime

# make follow-up short for test
os.environ["NOTIFIER_FOLLOWUP_SEC"] = "10"

from core.event_bus import bus
from modules.notifier import notifier

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("test_notifier")

async def main():
    await notifier.start()

    sample = {
        "mint": "test_mint_123",
        "symbol": "TST",
        "score": 50,
        "market_cap": 24000.0,
        "price": 0.000123,
    }

    # Emit a trade signal
    await bus.emit("TRADE_SIGNAL_READY", sample)
    logger.info("Emitted test TRADE_SIGNAL_READY")

    # Wait enough for follow-up to trigger (plus a bit)
    await asyncio.sleep(18)

    await notifier.stop()

if __name__ == '__main__':
    asyncio.run(main())
