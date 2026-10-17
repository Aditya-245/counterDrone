"""Task that drives every live session at a fixed 10 Hz."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.config import get_settings
from app.live import live_registry

logger = logging.getLogger("skygym.clock")

__all__ = ["run_session_clock"]


async def run_session_clock() -> None:
    """Advance every registered live session once per tick, forever.

    The engine is deterministic, so this loop only decides *when* a tick
    happens - never what it computes. If the loop falls behind, ticks are
    skipped rather than bunched, so a slow machine runs slower but never
    produces a different outcome.
    """
    period = 1.0 / max(1, get_settings().tick_hz)
    loop = asyncio.get_running_loop()
    next_tick = loop.time()
    while True:
        now = loop.time()
        if now < next_tick:
            await asyncio.sleep(next_tick - now)
            continue
        next_tick += period
        if next_tick < now - period:
            next_tick = now + period
        for live in live_registry.all():
            if not live.running:
                continue
            try:
                live.tick()
            except Exception:
                logger.exception("tick failed for session %s; stopping it", live.session_id)
                live.finish()
        await asyncio.sleep(0)


async def clock_stream() -> AsyncIterator[float]:
    """Yield the monotonic time of every tick. Used by tests that pump manually."""
    period = 1.0 / max(1, get_settings().tick_hz)
    loop = asyncio.get_running_loop()
    next_tick = loop.time()
    while True:
        now = loop.time()
        if now < next_tick:
            await asyncio.sleep(next_tick - now)
        next_tick += period
        yield now
