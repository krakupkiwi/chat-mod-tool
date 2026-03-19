"""
bench_fast_path.py — Micro-benchmark for the per-message fast path.

Usage:
    cd backend
    .venv/Scripts/python.exe benchmarks/bench_fast_path.py

Target: p99 < 500µs per message.
"""

import asyncio
import cProfile
import io
import pstats
import random
import string
import sys
import time

# Make sure the backend package is importable
sys.path.insert(0, ".")

import xxhash

from detection.engine import DetectionEngine
from pipeline.buffer import ChatBuffer
from pipeline.models import ChatMessage
from pipeline.normalizer import normalize_message, content_hash


# ---------------------------------------------------------------------------
# Synthetic message generator
# ---------------------------------------------------------------------------

BOT_PATTERNS = [
    "buy cheap followers at",
    "FREE V-BUCKS click here",
    "Check out my stream at",
    "follow me for follow back",
]

NORMAL_PHRASES = [
    "that was insane", "nice play", "POGGERS", "lol lmao",
    "good game everyone", "this streamer is so good",
    "first time watching", "hello from brazil",
    "how long has the stream been going",
    "great content keep it up",
]


def _random_username(bot: bool = False) -> str:
    if bot:
        prefix = random.choice(["bot", "spammer", "buyer"])
        suffix = "".join(random.choices(string.digits, k=6))
        return f"{prefix}{suffix}"
    words = ["happy", "sad", "viewer", "gamer", "fan", "watcher"]
    return random.choice(words) + str(random.randint(1, 9999))


def _make_message(i: int) -> ChatMessage:
    is_bot = i % 5 == 0
    is_dup = i % 10 == 0 and i > 0

    if is_bot:
        raw = random.choice(BOT_PATTERNS) + " example.com"
    elif is_dup:
        raw = "buy cheap followers at example.com"
    else:
        raw = random.choice(NORMAL_PHRASES)

    uid = f"u{i % 200}" if not is_bot else f"bot{i % 50}"
    username = _random_username(bot=is_bot)
    norm = normalize_message(raw)
    now = time.time() + i * 0.001

    return ChatMessage(
        user_id=uid,
        username=username,
        channel="testchannel",
        raw_text=raw,
        normalized_text=norm,
        content_hash=content_hash(norm),
        emoji_count=0,
        url_count=1 if "example.com" in raw else 0,
        mention_count=0,
        word_count=len(norm.split()),
        char_count=len(raw),
        caps_ratio=0.0,
        has_url="example.com" in raw,
        account_age_days=3 if is_bot else random.randint(30, 3000),
        is_moderator=False,
        is_vip=False,
        is_subscriber=False,
        received_at=now,
    )


async def run_benchmark(n: int = 10_000) -> None:
    print(f"\n{'='*60}")
    print(f"Fast-path benchmark: {n:,} messages")
    print("="*60)

    buf = ChatBuffer()
    engine = DetectionEngine(buf)

    # Pre-generate all messages to isolate detection time from construction time
    msgs = [_make_message(i) for i in range(n)]

    # Warmup (first 200 messages excluded from timing)
    for msg in msgs[:200]:
        await engine.process_message(msg)
    buf.prune()

    # --- Timed run ---
    latencies: list[float] = []
    t_total_start = time.perf_counter()
    for msg in msgs[200:]:
        t0 = time.perf_counter()
        await engine.process_message(msg)
        latencies.append((time.perf_counter() - t0) * 1_000_000)  # µs
    total_s = time.perf_counter() - t_total_start

    latencies.sort()
    n_measured = len(latencies)
    p = lambda pct: latencies[int(n_measured * pct / 100)]

    print(f"\nLatency per message (µs) over {n_measured:,} messages:")
    print(f"  min  = {latencies[0]:.1f}")
    print(f"  mean = {sum(latencies)/n_measured:.1f}")
    print(f"  p50  = {p(50):.1f}")
    print(f"  p95  = {p(95):.1f}")
    print(f"  p99  = {p(99):.1f}  {'PASS' if p(99) < 500 else 'FAIL (target < 500us)'}")
    print(f"  max  = {latencies[-1]:.1f}")
    print(f"\nThroughput: {n_measured / total_s:,.0f} msg/s")

    # Flag any function averaging > 50µs
    flag_threshold_avg_us = 50
    for name, avg in [("process_message total", sum(latencies)/n_measured)]:
        if avg > flag_threshold_avg_us:
            print(f"  WARN {name}: avg {avg:.1f}us > {flag_threshold_avg_us}us threshold")

    # --- cProfile ---
    print(f"\n{'='*60}")
    print("cProfile: top 20 cumulative-time functions")
    print("="*60)

    pr = cProfile.Profile()
    pr.enable()
    for msg in msgs[:2000]:
        await engine.process_message(msg)
    pr.disable()

    buf2 = io.StringIO()
    ps = pstats.Stats(pr, stream=buf2).sort_stats("cumulative")
    ps.print_stats(20)
    output = buf2.getvalue()
    # Filter out stdlib noise; show only lines with >0.001s cumtime
    lines = output.splitlines()
    for line in lines:
        print(line)


if __name__ == "__main__":
    asyncio.run(run_benchmark(10_000))
