"""
Evaluation harness — S11.

Runs a simulator scenario against the live detection engine, collects
threat_alert events from the backend WebSocket, then computes and prints
precision, recall, F1-score, and false-positive rate.

Usage:
    python evaluate.py \\
        --scenario scenarios/bot_raid.yaml \\
        --host 127.0.0.1 --port 7842 --secret <ipc_secret> \\
        --duration 120 \\
        --min-confidence 50

Output:
    ┌─────────────────────────────────────┐
    │  Evaluation Report                  │
    │  Scenario : bot_raid (120s)         │
    │  Injected : 200 normal, 50 bot      │
    │  Alerted  : 48 bot, 3 normal (FP)   │
    │                                     │
    │  Precision : 94.1%                  │
    │  Recall    : 96.0%                  │
    │  F1        : 95.0%                  │
    │  FP rate   : 1.5%                   │
    └─────────────────────────────────────┘
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

# Ensure simulator/ is importable when run directly
# Insert only the project root — inserting simulator/ itself would shadow the
# simulator package with the local simulator.py module.
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from simulator.config import SimulatedMessage
from simulator.orchestrator import Orchestrator, load_scenario

logger = logging.getLogger("evaluate")


# ---------------------------------------------------------------------------
# Alert collector — listens on the backend /ws for threat_alert events
# ---------------------------------------------------------------------------

class AlertCollector:
    """Connects to the backend WebSocket and records every threat_alert."""

    def __init__(self, host: str, port: int, secret: str, min_confidence: float) -> None:
        self._url = f"ws://{host}:{port}/ws?secret={secret}"
        self._min_confidence = min_confidence
        # user_id → highest confidence alert seen
        self.alerted: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._collect())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    async def _collect(self) -> None:
        import websockets

        logger.info("Alert collector connecting to %s", self._url)
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self._url,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    logger.info("Alert collector connected")
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        try:
                            event: dict = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        if event.get("type") != "threat_alert":
                            continue

                        user_id = event.get("user_id", "")
                        confidence = float(event.get("confidence", 0.0))

                        if confidence >= self._min_confidence and user_id:
                            prev = self.alerted.get(user_id, 0.0)
                            self.alerted[user_id] = max(prev, confidence)
                            logger.debug(
                                "Alert: user_id=%s confidence=%.1f", user_id, confidence
                            )
            except Exception as exc:
                if self._stop.is_set():
                    break
                logger.warning("Alert collector error (%s), reconnecting in 2s...", exc)
                await asyncio.sleep(2.0)


# ---------------------------------------------------------------------------
# Tracking adapter — wraps WebSocketAdapter, records injected user labels
# ---------------------------------------------------------------------------

class TrackingWSAdapter:
    """
    Wraps the WebSocketAdapter drain loop.
    Records every (user_id, label) that gets injected into the backend.
    """

    def __init__(self, host: str, port: int, secret: str) -> None:
        from simulator.output.websocket_adapter import WebSocketAdapter
        inject_url = f"ws://{host}:{port}/ws/inject?secret={secret}"
        self._ws = WebSocketAdapter(inject_url)
        # user_id → label ('normal' | 'spam' | 'bot_cluster' | ...)
        self.injected: dict[str, str] = {}

    async def drain_queue(
        self,
        queue: asyncio.Queue,
        stop: asyncio.Event,
        rate_limit_mpm: int = 0,
    ) -> None:
        await self._ws.connect()

        if rate_limit_mpm > 0:
            min_interval = 60.0 / rate_limit_mpm
        else:
            min_interval = 0.0

        last_send = 0.0

        while not stop.is_set() or not queue.empty():
            try:
                msg: SimulatedMessage = queue.get_nowait()
            except asyncio.QueueEmpty:
                if stop.is_set():
                    break
                await asyncio.sleep(0.005)
                continue

            # Track ground truth
            self.injected[msg.user_id] = msg.label

            if min_interval > 0:
                now = asyncio.get_event_loop().time()
                elapsed = now - last_send
                if elapsed < min_interval:
                    await asyncio.sleep(min_interval - elapsed)
                last_send = asyncio.get_event_loop().time()

            await self._ws.send(msg)

        await self._ws.close()
        logger.info(
            "Inject adapter done — sent=%d errors=%d",
            self._ws.stats["sent"],
            self._ws.stats["errors"],
        )


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(
    injected: dict[str, str],
    alerted: dict[str, float],
    min_confidence: float,
) -> dict[str, Any]:
    """
    Compare ground truth (injected labels) against what the engine flagged.

    A user is a 'positive' (bot) if their label != 'normal'.
    An alert fires when the user_id appears in alerted{} with
    confidence >= min_confidence (already filtered by AlertCollector).
    """
    bot_ids = {uid for uid, label in injected.items() if label != "normal"}
    normal_ids = {uid for uid, label in injected.items() if label == "normal"}
    alerted_ids = set(alerted.keys())

    tp = len(bot_ids & alerted_ids)        # bot detected  ✓
    fp = len(normal_ids & alerted_ids)     # normal flagged ✗
    fn = len(bot_ids - alerted_ids)        # bot missed ✗
    tn = len(normal_ids - alerted_ids)     # normal clean  ✓

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)
    fp_rate   = fp / len(normal_ids) if normal_ids else 0.0

    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "total_injected": len(injected),
        "total_normal": len(normal_ids),
        "total_bot": len(bot_ids),
        "total_alerted": len(alerted_ids),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "fp_rate": fp_rate,
    }


def print_report(
    scenario_name: str,
    duration: float,
    min_confidence: float,
    metrics: dict[str, Any],
) -> None:
    m = metrics
    width = 46

    def row(label: str, value: str) -> str:
        line = f"  {label:<22} {value}"
        return f"| {line:<{width - 4}} |"

    # Use ASCII box characters to avoid Windows cp1252 encoding issues
    sep = "-" * (width - 2)
    print(f"+{sep}+")
    print(f"|{'  Evaluation Report':<{width - 2}}|")
    print(f"|{'':<{width - 2}}|")
    print(row("Scenario", f"{scenario_name}"))
    print(row("Duration", f"{duration:.0f}s  |  min-confidence {min_confidence:.0f}"))
    print(f"|{'':<{width - 2}}|")
    print(row("Injected", f"{m['total_normal']} normal,  {m['total_bot']} bot"))
    print(row("Alerted", f"{m['total_alerted']} total"))
    print(row("  True positives (TP)", str(m["tp"])))
    print(row("  False positives (FP)", str(m["fp"])))
    print(row("  False negatives (FN)", str(m["fn"])))
    print(row("  True negatives (TN)", str(m["tn"])))
    print(f"|{'':<{width - 2}}|")
    print(row("Precision", f"{m['precision'] * 100:.1f}%"))
    print(row("Recall (detection rate)", f"{m['recall'] * 100:.1f}%"))
    print(row("F1 score", f"{m['f1'] * 100:.1f}%"))
    print(row("False-positive rate", f"{m['fp_rate'] * 100:.2f}%"))

    target_ok = m["fp_rate"] <= 0.05
    target_str = "PASS" if target_ok else "FAIL"
    print(f"|{'':<{width - 2}}|")
    print(row("FP < 5% target", target_str))
    print(f"+{sep}+")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Evaluation harness: run a scenario and measure detection accuracy."
    )
    p.add_argument("--scenario", required=True, help="Path to scenario YAML")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True, help="Backend port")
    p.add_argument("--secret", required=True, help="IPC secret")
    p.add_argument("--duration", type=float, default=120.0, help="Run duration in seconds")
    p.add_argument(
        "--grace",
        type=float,
        default=10.0,
        help="Extra seconds after scenario ends to collect late alerts",
    )
    p.add_argument(
        "--min-confidence",
        type=float,
        default=50.0,
        dest="min_confidence",
        help="Minimum alert confidence to count as a detection (default 50)",
    )
    p.add_argument("--rate", type=float, default=1.0, help="Rate multiplier")
    p.add_argument("--output", default=None, help="Write metrics JSON to this file")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


async def run(args: argparse.Namespace) -> dict[str, Any]:
    scenario_path = Path(args.scenario)
    if not scenario_path.exists():
        scenario_path = Path(__file__).parent / args.scenario
    if not scenario_path.exists():
        logger.error("Scenario not found: %s", args.scenario)
        sys.exit(1)

    config = load_scenario(str(scenario_path))

    # Override duration from CLI if provided
    if args.duration:
        config.duration_seconds = int(args.duration)

    logger.info(
        "Evaluating scenario '%s' (%ds) against %s:%d",
        config.name,
        config.duration_seconds,
        args.host,
        args.port,
    )

    # Start alert collector (listens on backend /ws)
    collector = AlertCollector(args.host, args.port, args.secret, args.min_confidence)
    await collector.start()

    # Give collector 1s to connect before we start injecting
    await asyncio.sleep(1.0)

    # Run scenario — tracking adapter records injected labels + injects to backend
    adapter = TrackingWSAdapter(args.host, args.port, args.secret)
    orch = Orchestrator(config, channel_id="eval_channel")

    t0 = time.time()
    await orch.run(output_adapter=adapter)
    elapsed = time.time() - t0

    logger.info(
        "Scenario complete in %.1fs. Waiting %.1fs grace period for late alerts...",
        elapsed,
        args.grace,
    )
    await asyncio.sleep(args.grace)
    await collector.stop()

    metrics = compute_metrics(adapter.injected, collector.alerted, args.min_confidence)

    print_report(config.name, elapsed, args.min_confidence, metrics)

    if args.output:
        import json as _json
        with open(args.output, "w") as f:
            _json.dump(metrics, f, indent=2)
        logger.info("Metrics written to %s", args.output)

    return metrics


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
