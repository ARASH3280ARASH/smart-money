from __future__ import annotations

"""
DBSCAN-based community detection on the wallet co-trade graph.
Assigns cluster IDs to wallets and persists them.
"""

from typing import Dict, List, Optional, Tuple

import networkx as nx
import numpy as np
from sklearn.cluster import DBSCAN
from sqlalchemy import select

from db.models import Wallet, WalletRelationship
from db.session import get_db
from graph.relationship import build_co_trade_graph, persist_relationships
from utils.logger import get_logger

logger = get_logger(__name__)


def detect_communities_dbscan(
    G: nx.Graph,
    eps: float = 0.4,
    min_samples: int = 2,
) -> Dict[str, int]:
    """
    Run DBSCAN on the graph adjacency matrix.
    Returns {wallet_address: cluster_id} mapping.
    -1 = noise (no cluster).
    """
    nodes = list(G.nodes())
    if len(nodes) < 2:
        return {}

    n = len(nodes)
    node_idx = {addr: i for i, addr in enumerate(nodes)}

    # Build distance matrix from graph (1 - normalized_weight = distance)
    dist_matrix = np.ones((n, n))
    np.fill_diagonal(dist_matrix, 0.0)

    max_weight = max(
        (d.get("weight", 0) for _, _, d in G.edges(data=True)), default=1.0
    )
    if max_weight == 0:
        max_weight = 1.0

    for u, v, data in G.edges(data=True):
        weight = data.get("weight", 0)
        normalized = weight / max_weight
        distance = 1.0 - normalized
        i, j = node_idx[u], node_idx[v]
        dist_matrix[i][j] = distance
        dist_matrix[j][i] = distance

    clustering = DBSCAN(eps=eps, min_samples=min_samples, metric="precomputed")
    labels = clustering.fit_predict(dist_matrix)

    result = {}
    for i, addr in enumerate(nodes):
        result[addr] = int(labels[i])

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = list(labels).count(-1)
    logger.info(
        "DBSCAN: %d clusters, %d noise nodes from %d wallets",
        n_clusters, n_noise, n,
    )

    return result


async def run_clustering_pipeline(chain: str) -> Dict[str, int]:
    """
    Full clustering pipeline:
    1. Build co-trade graph
    2. Persist relationships
    3. Run DBSCAN
    4. Persist cluster assignments
    Returns cluster mapping.
    """
    G = await build_co_trade_graph(chain)
    if G.number_of_nodes() < 2:
        logger.info("Not enough nodes to cluster on %s", chain)
        return {}

    await persist_relationships(G, chain)
    cluster_map = detect_communities_dbscan(G)
    await _persist_cluster_assignments(cluster_map, chain)

    return cluster_map


async def _persist_cluster_assignments(
    cluster_map: Dict[str, int], chain: str
) -> None:
    """Write cluster IDs to wallet_relationships table."""
    if not cluster_map:
        return

    async with get_db() as db:
        for addr, cluster_id in cluster_map.items():
            result = await db.execute(
                select(WalletRelationship).where(
                    (WalletRelationship.wallet_a == addr)
                    | (WalletRelationship.wallet_b == addr),
                    WalletRelationship.chain == chain,
                )
            )
            rels = result.scalars().all()
            for rel in rels:
                rel.cluster_id = cluster_id if cluster_id >= 0 else None


async def get_wallet_cluster(address: str, chain: str) -> Optional[int]:
    """Return the cluster ID for a wallet, or None if unclustered."""
    async with get_db() as db:
        result = await db.execute(
            select(WalletRelationship.cluster_id)
            .where(
                (WalletRelationship.wallet_a == address)
                | (WalletRelationship.wallet_b == address),
                WalletRelationship.chain == chain,
                WalletRelationship.cluster_id.isnot(None),
            )
            .limit(1)
        )
        row = result.scalar_one_or_none()
    return row


async def get_cluster_members(cluster_id: int, chain: str) -> List[str]:
    """Return all wallet addresses in a given cluster."""
    async with get_db() as db:
        result = await db.execute(
            select(WalletRelationship).where(
                WalletRelationship.chain == chain,
                WalletRelationship.cluster_id == cluster_id,
            )
        )
        rels = result.scalars().all()

    members = set()
    for r in rels:
        members.add(r.wallet_a)
        members.add(r.wallet_b)
    return list(members)


async def detect_cluster_buys(
    token_address: str,
    chain: str,
    window_hours: int = 4,
) -> Optional[Dict]:
    """
    Check if members of the same cluster bought the same token recently.
    Returns event dict if detected.
    """
    from datetime import datetime, timedelta
    from db.models import Trade, Wallet

    since = datetime.utcnow() - timedelta(hours=window_hours)

    async with get_db() as db:
        result = await db.execute(
            select(Trade, Wallet)
            .join(Wallet, Trade.wallet_id == Wallet.id)
            .where(
                Trade.token_address == token_address,
                Trade.chain == chain,
                Trade.trade_type == "buy",
                Trade.timestamp >= since,
            )
        )
        rows = result.all()

    if not rows:
        return None

    # Check if any buyers are in the same cluster
    buyer_clusters: Dict[int, List[str]] = {}
    for trade, wallet in rows:
        cluster_id = await get_wallet_cluster(wallet.address, chain)
        if cluster_id is not None and cluster_id >= 0:
            buyer_clusters.setdefault(cluster_id, []).append(wallet.address)

    if not buyer_clusters:
        return None

    # Find the largest cluster group buying
    best_cluster = max(buyer_clusters, key=lambda k: len(buyer_clusters[k]))
    cluster_buyers = buyer_clusters[best_cluster]

    if len(cluster_buyers) < 2:
        return None

    return {
        "event_type": "CLUSTER_BUY",
        "token_address": token_address,
        "chain": chain,
        "cluster_id": best_cluster,
        "cluster_buyer_count": len(cluster_buyers),
        "cluster_buyers": cluster_buyers,
        "total_clusters": len(buyer_clusters),
        "detected_at": datetime.utcnow(),
    }
