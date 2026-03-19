"""
bench_memory.py — Memory growth test for a 10-minute simulated stream.

Usage:
    cd backend
    .venv/Scripts/python.exe benchmarks/bench_memory.py

Simulates 1,000 msg/min for 10 minutes (10,000 total messages) with 200
cycling users.  Samples RSS every 10 seconds.  Flags if RSS grows > 20MB
over baseline (indicates a leak in _user_signals or buffer accumulation).
"""

import asyncio
import random
import sys
import time

sys.path.insert(0, ".")

try:
    import psutil
    _proc = psutil.Process()
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    print("WARNING: psutil not installed — memory tracking disabled")

from detection.engine import DetectionEngine
from pipeline.buffer import ChatBuffer
from pipeline.models import ChatMessage
from pipeline.normalizer import normalize_message, content_hash


MESSAGES = [
    "this is a great stream",
    "nice one", "let's go", "hello everyone",
    "first time watching", "great gameplay",
    "buy cheap followers now",
    "follow me for follow back please",
    "check out my channel at example.com",
    "PogChamp", "LUL", "KEKW",
]


def _make_msg(uid: str, content: str, ts: float) -> ChatMessage:
    norm = normalize_message(content)
    return ChatMessage(
        user_id=uid,
        username=f"user{uid}",
        channel="testchannel",
        raw_text=content,
        normalized_text=norm,
        content_hash=content_hash(norm),
        emoji_count=0,
        url_count=1 if "example.com" in content else 0,
        mention_count=0,
        word_count=len(norm.split()),
        char_count=len(content),
        caps_ratio=0.0,
        has_url="example.com" in content,
        account_age_days=random.randint(1, 2000),
        is_moderator=False,
        is_vip=False,
        is_subscriber=False,
        received_at=ts,
    )


def _rss_mb() -> float | None:
    if not HAS_PSUTIL:
        return None
    return _proc.memory_info().rss / 1_048_576


async def run_benchmark(
    total_messages: int = 10_000,
    msg_per_min: int = 1_000,
    n_users: int = 200,
    sample_interval_s: int = 10,
) -> None:
    print(f"\n{'='*60}")
    print(f"Memory growth benchmark")
    print(f"  {total_messages:,} messages @ {msg_per_min} msg/min")
    print(f"  {n_users} cycling users, {total_messages // msg_per_min} minute stream")
    print("="*60)

    if not HAS_PSUTIL:
        print("psutil not available — cannot measure RSS")
        return

    buf = ChatBuffer()
    engine = DetectionEngine(buf)

    # Message interval in seconds
    msg_interval = 60.0 / msg_per_min  # 0.06s per msg at 1K msg/min

    baseline_rss = _rss_mb()
    print(f"\nBaseline RSS: {baseline_rss:.1f} MB")

    samples: list[tuple[float, float]] = [(0.0, baseline_rss)]
    tick_counter = 0
    start_wall = time.monotonic()
    simulated_time = 0.0

    for i in range(total_messages):
        simulated_time = i * msg_interval
        uid = f"u{i % n_users}"
        msg = _make_msg(uid, random.choice(MESSAGES), time.time())
        buf.add(msg)
        buf.prune()
        await engine.process_message(msg)

        # Fire a tick every 60 messages (≈ every 3.6s at 1K msg/min → ~1 tick/s)
        tick_counter += 1
        if tick_counter % 17 == 0:  # ~17 msgs/tick at 1K msg/min gives ~1 tick/s
            await engine.tick()

        # Sample RSS every 10 simulated seconds
        sim_elapsed_min = simulated_time / 60
        sample_at = sample_interval_s * (len(samples))
        if simulated_time >= sample_at:
            rss = _rss_mb()
            samples.append((simulated_time, rss))
            growth = rss - baseline_rss
            print(f"  t={simulated_time/60:.1f}min  RSS={rss:.1f}MB  growth={growth:+.1f}MB")

        # Yield occasionally to keep asyncio healthy
        if i % 100 == 0:
            await asyncio.sleep(0)

    # Final sample
    final_rss = _rss_mb()
    samples.append((simulated_time, final_rss))

    wall_elapsed = time.monotonic() - start_wall
    total_growth = final_rss - baseline_rss
    max_rss = max(s[1] for s in samples)

    print(f"\n{'='*60}")
    print(f"Results:")
    print(f"  Wall time:    {wall_elapsed:.1f}s")
    print(f"  Baseline RSS: {baseline_rss:.1f} MB")
    print(f"  Final RSS:    {final_rss:.1f} MB")
    print(f"  Peak RSS:     {max_rss:.1f} MB")
    print(f"  Total growth: {total_growth:+.1f} MB  ", end="")
    if total_growth > 20:
        print(f"FAIL (> 20MB growth — possible leak)")
    else:
        print(f"PASS (<= 20MB growth)")

    print(f"\n  _user_signals entries: {len(engine._user_signals)}")
    print(f"  _user_timestamps entries: {len(engine._user_timestamps)}")
    print(f"  Buffer 300s: {engine._buffer.total_buffered} messages")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(run_benchmark())
