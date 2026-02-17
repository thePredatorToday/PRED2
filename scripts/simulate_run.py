import asyncio
import random
import string
import time
from datetime import datetime

from core.database import db
from core.system_state import state
from modules.hunter import HunterPayload, Hunter
from modules import notifier as notifier_module
from modules.executor import executor


async def simulate_one_cycle(hunter: Hunter):
    # Generate random token payload
    mint = "".join(random.choices(string.ascii_lowercase + string.digits, k=32))
    symbol = "T" + ''.join(random.choices(string.ascii_uppercase, k=3))
    price = round(random.uniform(0.00001, 0.001), 8)
    liquidity = random.uniform(500, 50000)
    market_cap = liquidity * random.uniform(10, 500)
    volume_24h = random.uniform(100, 20000)
    volume_5m = random.uniform(0, 5000)

    payload = {
        "mint": mint,
        "name": symbol + "Coin",
        "symbol": symbol,
        "price": price,
        "market_cap": market_cap,
        "liquidity": liquidity,
        "volume_24h": volume_24h,
        "volume_5m": volume_5m,
    }

    # Preliminary score using Hunter's private method
    try:
        validated = HunterPayload(**payload)
    except Exception:
        return None

    prelim = hunter._calculate_preliminary_score(validated)

    # Threshold slightly lower for simulation
    if prelim < 25:
        # Not interesting
        return None

    # Save signal to DB
    db.save_signal({
        "mint": mint,
        "symbol": symbol,
        "liquidity": liquidity,
        "market_cap": market_cap,
        "score": int(prelim),
    })

    # Emit notifier handling (saves prediction & schedules follow-up)
    await notifier_module.notifier._on_trade_signal({
        "mint": mint,
        "symbol": symbol,
        "score": prelim,
        "market_cap": market_cap,
        "price": price,
    })

    # Simulate immediate approval and execution
    approval_data = {
        "token_address": mint,
        "amount_sol": 0.05,
        "original_signal": {
            "token_address": mint,
            "score": prelim,
            "detailed_scores": {"sim": prelim},
        },
    }

    await executor.execute_trade(approval_data)

    # Schedule exit after random time
    delay = random.uniform(5, 20)
    async def close_after():
        await asyncio.sleep(delay)
        # Grab current price variation
        new_price = price * random.uniform(0.5, 3.0)
        # Call Executor exit
        await executor._exit_position(mint, new_price, reason="SIM_EXIT")

    asyncio.create_task(close_after())

    return mint


async def run_simulation(duration_sec: int = 60, rate_per_sec: float = 0.5):
    hunter = Hunter()

    end = time.time() + duration_sec
    created = 0
    processed = 0

    while time.time() < end:
        # Generate events at rate
        await simulate_one_cycle(hunter)
        processed += 1
        created += 1
        await asyncio.sleep(1.0 / max(0.1, rate_per_sec))

    # wait for pending exits/followups
    await asyncio.sleep(5)

    # Summarize DB stats
    conn = db.connect()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM signals")
    signals_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM trades")
    trades_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM predictions")
    preds_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM predictions WHERE result='hit'")
    preds_hit = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM predictions WHERE result='miss'")
    preds_miss = cursor.fetchone()[0]
    conn.close()

    print("Simulation complete:")
    print(f"  Signals saved: {signals_count}")
    print(f"  Trades recorded: {trades_count}")
    print(f"  Predictions: {preds_count} (hit: {preds_hit}, miss: {preds_miss})")
    print(f"  System balance (simulated): {state.balance_sol:.4f} SOL")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=int, default=60, help='Simulation duration seconds')
    parser.add_argument('--rate', type=float, default=0.5, help='Events per second')
    args = parser.parse_args()

    asyncio.run(run_simulation(duration_sec=args.duration, rate_per_sec=args.rate))
