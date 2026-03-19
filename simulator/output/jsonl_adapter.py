"""
JSONLAdapter — writes labeled messages to a gzip-compressed JSONL file.

Each line is a JSON object with the full SimulatedMessage fields + ground truth label.
Used to generate labeled datasets for offline evaluation and ML model training.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from dataclasses import asdict
from pathlib import Path

logger = logging.getLogger(__name__)


class JSONLAdapter:
    def __init__(self, output_path: str) -> None:
        self.path = Path(output_path)
        self._file = None
        self._write_count = 0

    def open(self) -> None:
        suffix = self.path.suffix
        if suffix == ".gz":
            self._file = gzip.open(self.path, "wt", encoding="utf-8")
        elif suffix in (".jsonl", ".json"):
            self._file = open(self.path, "w", encoding="utf-8")
        else:
            # Default: add .jsonl.gz
            gz_path = self.path.with_suffix(".jsonl.gz")
            self._file = gzip.open(gz_path, "wt", encoding="utf-8")
            self.path = gz_path
        logger.info("JSONL adapter writing to %s", self.path)

    def write(self, message: "SimulatedMessage") -> None:
        if self._file is None:
            raise RuntimeError("JSONLAdapter not opened — call open() first")
        record = asdict(message)
        self._file.write(json.dumps(record) + "\n")
        self._write_count += 1

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
        logger.info("JSONL adapter closed — wrote %d records to %s", self._write_count, self.path)

    @property
    def stats(self) -> dict:
        return {"written": self._write_count, "path": str(self.path)}

    # --- Drain loop ---

    async def drain_queue(
        self,
        queue: asyncio.Queue,
        stop: asyncio.Event,
    ) -> None:
        """Consume messages from the queue and write to JSONL."""
        self.open()
        try:
            while not stop.is_set() or not queue.empty():
                try:
                    msg = queue.get_nowait()
                    self.write(msg)
                except asyncio.QueueEmpty:
                    if stop.is_set():
                        break
                    await asyncio.sleep(0.005)
        finally:
            self.close()
