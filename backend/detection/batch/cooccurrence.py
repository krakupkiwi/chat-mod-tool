"""
CooccurrenceDetector — igraph-based bot network detection.

Detects bot networks that coordinate across multiple semantic clusters.
Runs AFTER SemanticClusterer — takes its cluster output as input.

Algorithm:
  1. Build a co-occurrence graph: each user is a node. Two users share an
     edge if they appear together in the same semantic cluster.
     Edge weight = number of shared clusters.
  2. Run igraph's Infomap community detection (better than Louvain for
     directed coordination graphs). Falls back to fast greedy if Infomap
     fails.
  3. Communities with >= MIN_COMMUNITY_SIZE distinct users that span
     multiple clusters are flagged as cross-cluster bot networks.

This catches sophisticated bot farms that deliberately spread across multiple
topic clusters to evade per-cluster detection.

Falls back silently if igraph is not installed — no functionality loss.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MIN_COMMUNITY_SIZE = 4   # minimum users to flag a community
MIN_SPANNING_CLUSTERS = 2  # community must span this many clusters


@dataclass
class BotNetwork:
    """A detected cross-cluster bot network."""
    network_id: str
    user_ids: list[str]
    size: int
    spanning_clusters: list[str]   # cluster IDs this network spans
    risk_score: float              # 0–25


@dataclass
class CooccurrenceResult:
    networks: list[BotNetwork] = field(default_factory=list)
    network_count: int = 0
    risk_score: float = 0.0


class CooccurrenceDetector:
    """
    igraph-based co-occurrence community detector.

    Stateless — each call to detect() is independent.
    """

    def __init__(self) -> None:
        self._igraph_available = self._check_igraph()

    @staticmethod
    def _check_igraph() -> bool:
        try:
            import igraph  # noqa: F401
            return True
        except ImportError:
            logger.info(
                "igraph not installed — cross-cluster bot network detection disabled. "
                "Install with: pip install igraph"
            )
            return False

    def detect(self, clusters: list[dict]) -> CooccurrenceResult:
        """
        Detect cross-cluster bot networks from DBSCAN semantic cluster output.

        clusters: list of cluster dicts as returned by SemanticClusterer.
            Each dict has 'cluster_id' and 'user_ids' keys.

        Returns CooccurrenceResult with any detected cross-cluster networks.
        """
        if not self._igraph_available or len(clusters) < MIN_SPANNING_CLUSTERS:
            return CooccurrenceResult()

        try:
            return self._run(clusters)
        except Exception:
            logger.exception("CooccurrenceDetector failed")
            return CooccurrenceResult()

    def _run(self, clusters: list[dict]) -> CooccurrenceResult:
        import igraph as ig

        # Collect all unique users and build user_id → index mapping
        all_users: list[str] = []
        user_idx: dict[str, int] = {}
        for cluster in clusters:
            for uid in cluster.get("user_ids", []):
                if uid not in user_idx:
                    user_idx[uid] = len(all_users)
                    all_users.append(uid)

        if len(all_users) < MIN_COMMUNITY_SIZE:
            return CooccurrenceResult()

        # Build edge list: (user_a_idx, user_b_idx) for each pair in each cluster
        # Use a dict to accumulate edge weights (number of shared clusters)
        edge_weights: dict[tuple[int, int], int] = {}
        user_cluster_membership: dict[str, list[str]] = {}

        for cluster in clusters:
            cid = cluster.get("cluster_id", "")
            members = cluster.get("user_ids", [])
            for uid in members:
                user_cluster_membership.setdefault(uid, []).append(cid)
            # Add edges for all pairs in this cluster
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = user_idx[members[i]], user_idx[members[j]]
                    key = (min(a, b), max(a, b))
                    edge_weights[key] = edge_weights.get(key, 0) + 1

        if not edge_weights:
            return CooccurrenceResult()

        edges = list(edge_weights.keys())
        weights = [edge_weights[e] for e in edges]

        G = ig.Graph(n=len(all_users), edges=edges, directed=False)
        G.es["weight"] = weights

        # Run Infomap for directed-flow-aware community detection
        try:
            communities = G.community_infomap(edge_weights="weight")
        except Exception:
            # Fallback to fast greedy (no weight support in all versions)
            communities = G.community_fastgreedy(weights="weight").as_clustering()

        networks: list[BotNetwork] = []
        for ci, community in enumerate(communities):
            if len(community) < MIN_COMMUNITY_SIZE:
                continue

            member_ids = [all_users[idx] for idx in community]
            # Find which clusters these members span
            spanning = set()
            for uid in member_ids:
                for cid in user_cluster_membership.get(uid, []):
                    spanning.add(cid)

            if len(spanning) < MIN_SPANNING_CLUSTERS:
                continue

            # Risk score: scales with size and number of clusters spanned
            risk = min(len(member_ids) * 1.5 + len(spanning) * 3.0, 25.0)

            networks.append(BotNetwork(
                network_id=f"net_{ci}",
                user_ids=member_ids,
                size=len(member_ids),
                spanning_clusters=sorted(spanning),
                risk_score=risk,
            ))
            logger.info(
                "BotNetwork detected: %d users spanning %d clusters (risk=%.1f)",
                len(member_ids), len(spanning), risk,
            )

        total_risk = min(sum(n.risk_score for n in networks), 25.0)
        return CooccurrenceResult(
            networks=networks,
            network_count=len(networks),
            risk_score=total_risk,
        )
