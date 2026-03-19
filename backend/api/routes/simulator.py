"""
Simulator control routes.

Allows the dashboard to start, stop, and poll the status of a bot attack
simulation. The simulator runs as a child subprocess that injects synthetic
messages into the live pipeline via /ws/inject.

Endpoints:
  GET  /api/simulator/status   — current state (idle | running) + metadata
  POST /api/simulator/start    — launch simulator subprocess
  POST /api/simulator/stop     — kill running subprocess

The inject WebSocket at /ws/inject is gated on settings.simulator_active,
which this router sets to True/False alongside the subprocess lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["simulator"])

# ---------------------------------------------------------------------------
# State (module-level singleton — one simulation at a time)
# ---------------------------------------------------------------------------

_process: asyncio.subprocess.Process | None = None
_start_time: float | None = None
_scenario_name: str = ""
_duration: float = 120.0

# Known scenarios (filename stem → display label)
SCENARIOS: dict[str, str] = {
    "normal_chat":    "Normal Chat",
    "spam_flood":     "Spam Flood",
    "bot_raid":       "Bot Raid",
    "5000_mpm_mixed": "5K msg/min Mixed",
}


def _simulator_dir() -> Path:
    """Absolute path to the simulator/ directory (sibling of backend/)."""
    return Path(__file__).parent.parent.parent.parent / "simulator"


def _scenario_path(stem: str) -> Path:
    return _simulator_dir() / "scenarios" / f"{stem}.yaml"


def _is_running() -> bool:
    return _process is not None and _process.returncode is None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SimulatorStatus(BaseModel):
    state: Literal["idle", "running"]
    scenario: str = ""
    elapsed: float = 0.0
    duration: float = 0.0
    scenarios: dict[str, str] = Field(default_factory=dict)


class StartRequest(BaseModel):
    scenario: str = "bot_raid"
    duration: float = Field(default=120.0, ge=10.0, le=600.0)
    rate: float = Field(default=1.0, ge=0.25, le=5.0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/simulator/status", response_model=SimulatorStatus)
async def get_status() -> SimulatorStatus:
    elapsed = (time.time() - _start_time) if _start_time and _is_running() else 0.0
    return SimulatorStatus(
        state="running" if _is_running() else "idle",
        scenario=_scenario_name if _is_running() else "",
        elapsed=round(elapsed, 1),
        duration=_duration,
        scenarios=SCENARIOS,
    )


@router.post("/simulator/start", response_model=SimulatorStatus)
async def start_simulation(req: StartRequest) -> SimulatorStatus:
    global _process, _start_time, _scenario_name, _duration

    # Kill any existing process first
    if _is_running():
        await _kill()

    scenario = req.scenario if req.scenario in SCENARIOS else "bot_raid"
    spath = _scenario_path(scenario)
    if not spath.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Scenario file not found: {spath}")

    # Enable inject endpoint
    settings.simulator_active = True

    cmd = [
        sys.executable,
        str(_simulator_dir() / "simulator.py"),
        "--scenario", str(spath),
        "--duration", str(req.duration),
        "--rate", str(req.rate),
        "--host", "127.0.0.1",
        "--port", str(settings.port),
        "--secret", _get_ipc_secret(),
    ]

    logger.info(
        "Starting simulator: scenario=%s duration=%.0fs rate=%.2fx",
        scenario, req.duration, req.rate,
    )

    _process = await asyncio.create_subprocess_exec(
        *cmd,
        # DEVNULL — we don't consume simulator output so PIPE would cause the
        # subprocess to block when the OS pipe buffer fills, preventing it from
        # exiting and making the elapsed timer run forever.
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(_simulator_dir()),
    )
    _start_time = time.time()
    _scenario_name = scenario
    _duration = req.duration

    # Background task: wait for process to finish, then clean up
    asyncio.create_task(_watch_process(), name="simulator-watcher")

    return SimulatorStatus(
        state="running",
        scenario=scenario,
        elapsed=0.0,
        duration=req.duration,
        scenarios=SCENARIOS,
    )


@router.post("/simulator/stop", response_model=SimulatorStatus)
async def stop_simulation() -> SimulatorStatus:
    if _is_running():
        await _kill()
    return SimulatorStatus(state="idle", scenarios=SCENARIOS)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _kill() -> None:
    global _process, _start_time, _scenario_name
    if _process is None:
        return
    try:
        _process.terminate()
        try:
            await asyncio.wait_for(_process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            _process.kill()
    except ProcessLookupError:
        pass
    _process = None
    _start_time = None
    _scenario_name = ""
    settings.simulator_active = False
    logger.info("Simulator stopped")


async def _watch_process() -> None:
    """Wait for the subprocess to exit naturally, then clean up state."""
    global _process, _start_time, _scenario_name
    if _process is None:
        return
    await _process.wait()
    _process = None
    _start_time = None
    _scenario_name = ""
    settings.simulator_active = False
    logger.info("Simulator finished (natural exit)")


def _get_ipc_secret() -> str:
    """Read IPC_SECRET from backend/.env — same file main.py writes."""
    env_file = Path(__file__).parent.parent.parent / ".env"
    try:
        for line in env_file.read_text().splitlines():
            if line.startswith("IPC_SECRET="):
                return line[len("IPC_SECRET="):]
    except Exception:
        pass
    return ""
