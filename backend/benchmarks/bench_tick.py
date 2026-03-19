"""
bench_tick.py — Micro-benchmark for the 1-second tick loop.

Usage:
    cd backend
    .venv/Scripts/python.exe benchmarks/bench_tick.py

Target: p99 < 40ms.
"""

import asyncio
import random
import sys
import time

sys.path.insert(0, ".")

from detection.engine import DetectionEngine
from pipeline.buffer import ChatBuffer
from pipeline.models import ChatMessage
from pipeline.normalizer import normalize_message, content_hash


def _make_msg(uid: str, content: str, ts: float) -> ChatMessage:
    norm = normalize_message(content)
    return ChatMessage(
        user_id=uid,
        username=f"user_{uid}",
        channel="testchannel",
        raw_text=content,
        normalized_text=norm,
        content_hash=content_hash(norm),
        emoji_count=0,
        url_count=0,
        mention_count=0,
        word_count=len(norm.split()),
        char_count=len(content),
        caps_ratio=0.0,
        has_url=False,
        account_age_days=random.randint(10, 1000),
        is_moderator=False,
        is_vip=False,
        is_subscriber=False,
        received_at=ts,
    )


MESSAGES_POOL = [
    "this stream is so good", "let's go", "nice play",
    "hello everyone", "how long has this been on",
    "first time watching", "great content",
    "buy followers at example.com",
    "check my profile for free stuff",
    "follow me for follow back",
]


async def _populate_engine(engine: DetectionEngine, buf: ChatBuffer,
                            n_messages: int = 2500, n_users: int = 200) -> None:
    """Pre-load engine with 30s worth of messages at 5K msg/min rate."""
    now = time.time()
    # Spread 2500 messages over 30 seconds (≈ 5K msg/min)
    for i in range(n_messages):
        uid = f"u{i % n_users}"
        msg = _make_msg(uid, random.choice(MESSAGES_POOL), now - (30 - i * 30 / n_messages))
        buf.add(msg)
        await engine.process_message(msg)
    buf.prune()


async def run_benchmark(n_ticks: int = 60) -> None:
    print(f"\n{'='*60}")
    print(f"Tick-loop benchmark: {n_ticks} ticks (simulated 5K msg/min load)")
    print("="*60)

    buf = ChatBuffer()
    engine = DetectionEngine(buf)

    # Populate with ~30s of messages at 5K msg/min
    print("Populating engine with 2500 messages (30s at 5K msg/min)...")
    await _populate_engine(engine, buf, n_messages=2500, n_users=200)

    # Add a few more messages between ticks to simulate ongoing load
    async def _add_messages_between_ticks() -> None:
        now = time.time()
        for j in range(8):  # ~8 msgs/tick ≈ 480/min
            uid = f"u{j % 200}"
            msg = _make_msg(uid, random.choice(MESSAGES_POOL), now)
            buf.add(msg)
            await engine.process_message(msg)

    # --- Warmup ---
    for _ in range(3):
        await _add_messages_between_ticks()
        await engine.tick()

    # --- Timed run ---
    durations: list[float] = []
    for tick_i in range(n_ticks):
        await _add_messages_between_ticks()
        t0 = time.perf_counter()
        await engine.tick()
        duration_ms = (time.perf_counter() - t0) * 1000
        durations.append(duration_ms)
        await asyncio.sleep(0)  # mimic real tick spacing

    durations.sort()
    n = len(durations)
    p = lambda pct: durations[int(n * pct / 100)]

    print(f"\nTick duration (ms) over {n} ticks:")
    print(f"  min  = {durations[0]:.2f}")
    print(f"  mean = {sum(durations)/n:.2f}")
    print(f"  p50  = {p(50):.2f}")
    print(f"  p95  = {p(95):.2f}")
    print(f"  p99  = {p(99):.2f}  {'PASS' if p(99) < 40 else 'FAIL (target < 40ms)'}")
    print(f"  max  = {durations[-1]:.2f}")

    if p(95) > 30:
        print("\nWARN p95 > 30ms — slowest sub-operation analysis:")
        # Run a single tick with per-op timing via the built-in breakdown
        buf2 = ChatBuffer()
        e2 = DetectionEngine(buf2)
        await _populate_engine(e2, buf2, n_messages=2500)
        # The tick() method now records _tick_breakdown in the health payload
        # We can read it from the ws_manager mock
        print("  (see tick_breakdown keys in health_update WS payload)")


if __name__ == "__main__":
    asyncio.run(run_benchmark(60))
