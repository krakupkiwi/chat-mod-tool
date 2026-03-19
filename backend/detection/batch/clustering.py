"""
SemanticClusterer â MiniLM ONNX + DBSCAN.

Detects groups of messages that are semantically similar but not textually
identical. Catches paraphrasing bots that vary their template to evade
exact-hash and MinHash detection.

Runs every 10 seconds on the last 30 seconds of messages.
Executes in a ThreadPoolExecutor â never blocks the event loop.

Adaptive sampling:
  <= 200 messages: embed all
  > 200 messages: embed flagged + random 20% sample
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.models import ChatMessage

logger = logging.getLogger(__name__)

FULL_EMBED_THRESHOLD = 100   # reduced from 200: keeps embedding under 8s at typical volumes
SAMPLE_RATIO_HIGH = 0.10    # reduced from 0.20: 10% sample above threshold
MAX_CLUSTER_SAMPLE = 2000  # hard cap before encoding to prevent O(nÂ²) DBSCAN stalls
MIN_CLUSTER_MEMBERS = 3
DBSCAN_EPS = 0.20          # cosine distance; similarity >= 0.80
DBSCAN_MIN_SAMPLES = 3

# Messages that look like viewer reactions are excluded from semantic clustering.
# Mirrors DetectionEngine._is_short_reaction() criteria.
_REACTION_MAX_CHARS = 25
_REACTION_MAX_WORDS = 3

# If a cluster contains more than this fraction of the active distinct senders,
# it is likely organic audience consensus (e.g. everyone reacting to the same
# stream moment), not a coordinated bot campaign.
ORGANIC_CLUSTER_RATIO = 0.15

_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "models", "minilm"
)


@dataclass
class ClusterResult:
    cluster_count: int
    clustered_ratio: float
    clusters: list[dict] = field(default_factory=list)
    risk_score: float = 0.0   # 0â25


class SemanticClusterer:
    def __init__(self) -> None:
        self._model = None
        self._use_fastembed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="minilm")
        self.last_result: ClusterResult = ClusterResult(0, 0.0)

    def _load_model(self) -> None:
        if self._model is not None:
            return

        # Try fastembed first - pre-quantized ONNX, no manual export required.
        # Falls back to sentence-transformers if fastembed is not installed or
        # if the model download fails (e.g. offline environment).
        try:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(
                model_name="BAAI/bge-small-en-v1.5",
                # Cache in the same models dir as the manual ONNX export
                cache_dir=os.path.join(os.path.dirname(_MODEL_DIR), "..", "..", "models"),
            )
            self._use_fastembed = True
            logger.info("SemanticClusterer: fastembed TextEmbedding loaded (BAAI/bge-small-en-v1.5)")
            return
        except Exception as exc:
            logger.info("fastembed not available (%s) - using sentence-transformers", exc)

        from sentence_transformers import SentenceTransformer

        try:
            self._model = SentenceTransformer(_MODEL_DIR, backend="onnx")
            logger.info("MiniLM loaded with ONNX backend from %s", _MODEL_DIR)
        except Exception:
            try:
                self._model = SentenceTransformer(_MODEL_DIR)
                logger.info("MiniLM loaded (no ONNX) from %s", _MODEL_DIR)
            except Exception:
                self._model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
                logger.info("MiniLM loaded from HuggingFace Hub (fallback)")

    @staticmethod
    def _is_organic_reaction(msg: "ChatMessage") -> bool:
        """
        Return True for messages that look like viewer reactions.

        Mirrors DetectionEngine._is_short_reaction(): short text with no URLs
        or @mentions.  These are excluded from semantic clustering because MiniLM
        embeds "lol", "lmao", "PogChamp", and similar single-emote messages to
        nearly identical vectors, causing every emote wave to appear as a cluster.
        """
        if getattr(msg, "url_count", 0) > 0 or getattr(msg, "mention_count", 0) > 0:
            return False
        return (
            getattr(msg, "word_count", 999) <= _REACTION_MAX_WORDS
            or getattr(msg, "char_count", 999) <= _REACTION_MAX_CHARS
        )

    async def analyze(self, messages: list["ChatMessage"]) -> ClusterResult:
        """Non-blocking: runs DBSCAN in thread pool."""
        # Strip short reactions before any sampling â they produce false clusters.
        eligible = [m for m in messages if not self._is_organic_reaction(m)]
        if len(eligible) < 5:
            return ClusterResult(0, 0.0)

        sample = self._get_sample(eligible)

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                self._executor, self._run_clustering, sample
            )
        except Exception:
            logger.exception("SemanticClusterer failed")
            result = ClusterResult(0, 0.0)

        self.last_result = result
        return result

    def _get_sample(self, messages: list["ChatMessage"]) -> list["ChatMessage"]:
        if len(messages) <= FULL_EMBED_THRESHOLD:
            return messages
        flagged = [m for m in messages if getattr(m, "minhash_flagged", False)]
        unflagged = [m for m in messages if not getattr(m, "minhash_flagged", False)]
        sample_size = max(50, int(len(unflagged) * SAMPLE_RATIO_HIGH))
        sample = random.sample(unflagged, min(sample_size, len(unflagged)))
        combined = flagged + sample
        # Hard cap: prevent O(nÂ²) DBSCAN stall at extreme volumes (> 2K msg/min sustained)
        if len(combined) > MAX_CLUSTER_SAMPLE:
            combined = random.sample(combined, MAX_CLUSTER_SAMPLE)
        return combined

    def _run_clustering(self, messages: list["ChatMessage"]) -> ClusterResult:
        """CPU-bound â runs in thread pool."""
        from sklearn.cluster import DBSCAN

        self._load_model()

        contents = [m.normalized_text for m in messages]
        user_ids = [m.user_id for m in messages]

        if self._use_fastembed:
            import numpy as np
            # fastembed returns a generator; collect into array, already L2-normalized
            embeddings = np.array(list(self._model.embed(contents)))
        else:
            embeddings = self._model.encode(
                contents,
                normalize_embeddings=True,
                batch_size=64,
                show_progress_bar=False,
            )

        labels = DBSCAN(
            eps=DBSCAN_EPS,
            min_samples=DBSCAN_MIN_SAMPLES,
            metric="cosine",
            algorithm="brute",
        ).fit_predict(embeddings)

        cluster_users: dict[int, list[str]] = defaultdict(list)
        cluster_msgs: dict[int, list[str]] = defaultdict(list)
        for idx, label in enumerate(labels):
            if label >= 0:
                cluster_users[int(label)].append(user_ids[idx])
                cluster_msgs[int(label)].append(contents[idx])

        clusters = []
        for cid, users in cluster_users.items():
            distinct = list(set(users))
            if len(distinct) >= MIN_CLUSTER_MEMBERS:
                clusters.append(
                    {
                        "cluster_id": f"sem_{cid}",
                        "user_ids": distinct,
                        "size": len(distinct),
                        "sample_message": cluster_msgs[cid][0],
                    }
                )

        total = len(messages)
        clustered_count = sum(len(c["user_ids"]) for c in clusters)
        clustered_ratio = clustered_count / total if total > 0 else 0.0

        # Normalize by distinct active senders so that large fractions of the
        # audience saying similar things (organic consensus) score low.
        active_users = len(set(user_ids))
        suspicious_clusters = []
        for c in clusters:
            cluster_fraction = c["size"] / active_users if active_users > 0 else 0.0
            if cluster_fraction <= ORGANIC_CLUSTER_RATIO:
                suspicious_clusters.append(c)
            else:
                logger.debug(
                    "Cluster %s covers %.0f%% of active senders â treating as organic consensus",
                    c["cluster_id"], cluster_fraction * 100,
                )
        clusters = suspicious_clusters

        risk = min(clustered_ratio * 40 + len(clusters) * 3, 25.0)

        logger.debug(
            "SemanticClusterer: %d messages â %d clusters, risk=%.1f",
            total, len(clusters), risk,
        )
        return ClusterResult(
            cluster_count=len(clusters),
            clustered_ratio=clustered_ratio,
            clusters=clusters,
            risk_score=risk,
        )
