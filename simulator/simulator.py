"""
Simulator CLI entry point.

Usage:
    python simulator.py --scenario scenarios/bot_raid.yaml --duration 120
    python simulator.py --scenario scenarios/normal_chat.yaml --output out.jsonl
    python simulator.py --scenario scenarios/spam_flood.yaml --host 127.0.0.1 --port 7842 --secret <ipc_secret>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure simulator/ is on path
sys.path.insert(0, str(Path(__file__).parent))

from runner import SimulatorRunner, load_scenario, make_jsonl_adapter, make_websocket_adapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("simulator")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TwitchIDS bot attack simulator")
    p.add_argument("--scenario", required=True, help="Path to scenario YAML file")
    p.add_argument("--duration", type=float, default=120.0, help="Duration in seconds")
    p.add_argument("--rate", type=float, default=1.0, help="Rate multiplier (1.0 = normal)")
    p.add_argument("--output", default=None, help="Write JSONL output to file")
    p.add_argument("--host", default="127.0.0.1", help="Backend host for WS inject")
    p.add_argument("--port", type=int, default=None, help="Backend port for WS inject")
    p.add_argument("--secret", default=None, help="IPC secret for WS inject")
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    scenario_path = Path(args.scenario)
    if not scenario_path.exists():
        # Try relative to simulator/ directory
        scenario_path = Path(__file__).parent / args.scenario
    if not scenario_path.exists():
        logger.error("Scenario file not found: %s", args.scenario)
        sys.exit(1)

    scenario = load_scenario(str(scenario_path))
    logger.info("Loaded scenario: %s", scenario.get("name", scenario_path.stem))

    runner = SimulatorRunner(scenario, rate_multiplier=args.rate)

    if args.output:
        logger.info("Output adapter: JSONL → %s", args.output)
        runner.add_adapter(make_jsonl_adapter(args.output))
    elif args.port and args.secret:
        logger.info("Output adapter: WebSocket → %s:%d", args.host, args.port)
        runner.add_adapter(make_websocket_adapter(args.host, args.port, args.secret))
    else:
        # Default: log-only (dry run for testing)
        async def log_adapter(msg):
            logger.debug("[%s] %s: %s", msg.label, msg.username, msg.content[:80])
        runner.add_adapter(log_adapter)
        logger.info("No output adapter — logging only (add --output or --port/--secret)")

    await runner.run(args.duration)


if __name__ == "__main__":
    asyncio.run(main())
