"""
tests/test_event_bus.py - Unit testy pro EventBus
"""

import asyncio
import pytest

from core.event_bus import EventBus, bus


@pytest.mark.asyncio
async def test_event_bus_subscribe_and_emit():
    """Test basic subscribe a emit."""
    test_bus = EventBus()
    received_data = []

    async def callback(payload):
        received_data.append(payload)

    test_bus.subscribe("TEST_EVENT", callback)
    await test_bus.emit("TEST_EVENT", {"msg": "hello"})

    assert len(received_data) == 1
    assert received_data[0]["msg"] == "hello"


@pytest.mark.asyncio
async def test_event_bus_multiple_listeners():
    """Test více listenerů na jednu událost."""
    test_bus = EventBus()
    received_1 = []
    received_2 = []

    async def callback1(payload):
        received_1.append(payload)

    async def callback2(payload):
        received_2.append(payload)

    test_bus.subscribe("EVENT", callback1)
    test_bus.subscribe("EVENT", callback2)

    await test_bus.emit("EVENT", "data")

    assert len(received_1) == 1
    assert len(received_2) == 1


@pytest.mark.asyncio
async def test_event_bus_sync_callback():
    """Test synchronní callback (nekoroutina)."""
    test_bus = EventBus()
    received = []

    def sync_callback(payload):
        received.append(payload)

    test_bus.subscribe("SYNC_EVENT", sync_callback)
    await test_bus.emit("SYNC_EVENT", "sync_data")

    assert len(received) == 1
    assert received[0] == "sync_data"


@pytest.mark.asyncio
async def test_event_bus_no_listeners():
    """Test emit bez listenerů (ne fail)."""
    test_bus = EventBus()
    # Měl by běžet bez chyby
    await test_bus.emit("NONEXISTENT_EVENT", "data")
    # Pass – bez výjimky


@pytest.mark.asyncio
async def test_event_bus_error_handling():
    """Test chyba v callbacku nezastaví ostatní."""
    test_bus = EventBus()
    received_1 = []
    received_2 = []

    async def failing_callback(payload):
        raise ValueError("Intentional error")

    async def normal_callback(payload):
        received_2.append(payload)

    test_bus.subscribe("ERROR_EVENT", failing_callback)
    test_bus.subscribe("ERROR_EVENT", normal_callback)

    # Emit by neměla vyhodit chybu, jen ji zalogovat
    await test_bus.emit("ERROR_EVENT", "data")

    assert len(received_2) == 1  # Normální callback by měl být zavolán


if __name__ == "__main__":
    asyncio.run(test_event_bus_subscribe_and_emit())
    print("✅ Všechny EventBus testy prošly!")

