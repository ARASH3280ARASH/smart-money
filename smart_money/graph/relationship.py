from __future__ import annotations

"""
Builds a weighted wallet co-trade graph.
Two wallets are connected if they traded the same token within a time window.
Edge weight = co-trade frequency × average wallet quality.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx
from sqlalchemy import select

from db.models import Trade, Wallet, WalletRelationship
from db.session import get_db
from utils.logger import get_logger

logger = get_logger(__name__)


async def build_co_trade_graph(
    chain: str,
    days: int = 30,
    min_score: float = 50.0,
    window_hours: int = 4,
) -> nx.Graph:
    """
    Build a NetworkX graph where nodes = wallet addresses,
    edges = co-traded same token within window_hours.
    """
    since = datetime.utcnow() - timedelta(days=days)
    G = nx.Graph()

    async with get_db() as db:
        # Fetch all trades from scored wallets
        result = await db.execute(
            select(Trade, Wallet)
            .join(Wallet, Trade.wallet_id == Wallet.id)
            .where(
                Trade.chain == chain,
                Trade.timestamp >= since,
                Wallet.score >= min_score,
                Wallet.is_active == True,
            )
            .order_by(Trade.token_address, Trade.timestamp)
        )
        rows = result.all()

    if not rows:
        logger.info("No trades found for graph on %s", chain)
        return G

    # Group by token
    by_token: Dict[str, List[Tuple[str, float, datetime]]] = {}
    for trade, wallet in rows:
        key = trade.token_address
        by_token.setdefault(key, []).append(
            (wallet.address, wallet.score, trade.timestamp)
        )

    edge_counts: Dict[Tuple[str, str], int] = {}
    edge_tokens: Dict[Tuple[str, str], Set[str]] = {}
    window = timedelta(hours=window_hours)

    for token_addr, entries in by_token.items():
        entries.sort(key=lambda x: x[2])  # sort by timestamp

        for i in range(len(entries)):
            addr_i, score_i, ts_i = entries[i]
            G.add_node(addr_i, score=score_i)
            for j in range(i + 1, len(entries)):
                addr_j, score_j, ts_j = entries[j]
                if ts_j - ts_i > window:
                    break
                G.add_node(addr_j, score=score_j)
                if addr_i != addr_j:
                    key_pair = (
                        min(addr_i, addr_j),
                        max(addr_i, addr_j),
                    )
                    edge_counts[key_pair] = edge_counts.get(key_pair, 0) + 1
                    edge_tokens.setdefault(key_pair, set()).add(token_addr)

    # Add edges with weights
    for (a, b), count in edge_counts.items():
        score_a = G.nodes[a].get("score", 0)
        score_b = G.nodes[b].get("score", 0)
        avg_score = (score_a + score_b) / 2
        rel_score = min((count / 5) * (avg_score / 100), 100)
        G.add_edge(a, b, weight=rel_score, co_trade_count=count,
                   shared_tokens=list(edge_tokens[(a, b)]))

    logger.info(
        "Built co-trade graph: %d nodes, %d edges on %s",
        G.number_of_nodes(), G.number_of_edges(), chain,
    )
    return G


async def persist_relationships(G: nx.Graph, chain: str) -> int:
    """Save graph edges to wallet_relationships table. Returns count saved."""
    import json
    count = 0

    async with get_db() as db:
        for u, v, data in G.edges(data=True):
            rel_score = data.get("weight", 0)
            co_count = data.get("co_trade_count", 0)
            shared = data.get("shared_tokens", [])

            # Check existing
            result = await db.execute(
                select(WalletRelationship).where(
                    WalletRelationship.wallet_a == u,
                    WalletRelationship.wallet_b == v,
                    WalletRelationship.chain == chain,
                )
            )
            rel = result.scalar_one_or_none()
            if rel is None:
                rel = WalletRelationship(
                    wallet_a=u,
                    wallet_b=v,
                    chain=chain,
                )
                db.add(rel)

            rel.relationship_score = rel_score
            rel.co_trade_count = co_count
            rel.shared_tokens_json = json.dumps(shared[:10])
            rel.last_seen = datetime.utcnow()
            count += 1

    logger.info("Persisted %d wallet relationships on %s", count, chain)
    return count


async def get_related_wallets(
    address: str, chain: str, min_score: float = 30.0
) -> List[Dict]:
    """Return wallets related to the given address."""
    async with get_db() as db:
        result = await db.execute(
            select(WalletRelationship)
            .where(
                (WalletRelationship.wallet_a == address)
                | (WalletRelationship.wallet_b == address),
                WalletRelationship.chain == chain,
                WalletRelationship.relationship_score >= min_score,
            )
            .order_by(WalletRelationship.relationship_score.desc())
            .limit(20)
        )
        rels = result.scalars().all()

    results = []
    for r in rels:
        peer = r.wallet_b if r.wallet_a == address else r.wallet_a
        results.append({
            "address": peer,
            "relationship_score": r.relationship_score,
            "co_trade_count": r.co_trade_count,
            "cluster_id": r.cluster_id,
        })
    return results
