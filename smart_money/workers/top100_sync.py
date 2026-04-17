from __future__ import annotations

"""
Top-100 wallet sync worker.
Every 10 min: enriches all tracked wallets, recomputes scores, re-ranks top-100.
Also seeds new wallets from Moralis discovery.
"""

import asyncio
from datetime import datetime, timedelta
from typing import List

from sqlalchemy import select

from analytics.scoring import batch_update_scores, rank_and_flag_top100
from analytics.wallet_analytics import compute_wallet_metrics
from config.settings import get_settings
from db.models import Wallet, WalletMetrics
from db.session import get_db
from ingestion.wallet_fetcher import (
    fetch_and_store_pnl,
    fetch_and_store_wallet_history,
    upsert_wallet,
)
from utils.logger import get_logger

logger = get_logger(__name__)


async def enrich_wallet(wallet: Wallet) -> None:
    """Full enrichment: history + PnL + metric recompute."""
    try:
        await fetch_and_store_wallet_history(wallet, days_back=90)
        await fetch_and_store_pnl(wallet)
        await compute_wallet_metrics(wallet.id)
    except Exception as e:
        logger.warning("Enrichment failed for wallet %s: %s", wallet.address[:10], e)


async def run_top100_sync() -> None:
    """Full top-100 sync pass."""
    settings = get_settings()
    logger.info("Starting top-100 sync")

    # 1. Fetch all tracked wallets
    async with get_db() as db:
        result = await db.execute(
            select(Wallet).where(Wallet.is_active == True).order_by(
                Wallet.last_synced.asc().nullsfirst()
            )
        )
        all_wallets: List[Wallet] = result.scalars().all()

    logger.info("Enriching %d wallets", len(all_wallets))

    # 2. Enrich in controlled batches
    batch_size = 5
    for i in range(0, len(all_wallets), batch_size):
        batch = all_wallets[i : i + batch_size]
        await asyncio.gather(*[enrich_wallet(w) for w in batch])
        await asyncio.sleep(1.0)  # Pause between batches

    # 3. Recompute all scores
    wallet_ids = [w.id for w in all_wallets]
    await batch_update_scores(wallet_ids)

    # 4. Re-rank and flag top-100
    top_wallets = await rank_and_flag_top100(settings.top_wallet_count)

    # 5. Sync top-100 addresses to Moralis Stream
    if settings.moralis_stream_id and top_wallets:
        try:
            from clients.streams_client import get_streams_client
            sc = get_streams_client()
            addresses = [w.address for w in top_wallets]
            await sc.sync_wallet_addresses(settings.moralis_stream_id, addresses)
        except Exception as e:
            logger.warning("Stream address sync failed: %s", e)

    logger.info(
        "Top-100 sync complete. Top wallet: score=%.1f addr=%s",
        top_wallets[0].score if top_wallets else 0,
        top_wallets[0].address[:12] if top_wallets else "N/A",
    )


async def seed_initial_wallets(seed_token_addresses: List[str], chain: str) -> int:
    """
    Seed wallet list from top traders of seed tokens.
    Run once at startup if wallet table is empty.
    """
    from ingestion.token_fetcher import discover_wallets_from_token

    count = 0
    for token_addr in seed_token_addresses:
        try:
            pairs = await discover_wallets_from_token(token_addr, chain)
            for addr, c in pairs:
                w = await upsert_wallet(addr, c)
                count += 1
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.warning("Seed discovery failed for %s: %s", token_addr[:10], e)

    logger.info("Seeded %d wallets from %d tokens", count, len(seed_token_addresses))
    return count


async def maybe_seed_wallets() -> None:
    """Seed wallets if DB is nearly empty."""
    async with get_db() as db:
        result = await db.execute(select(Wallet))
        wallets = result.scalars().all()

    if len(wallets) < 10:
        settings = get_settings()
        for chain in settings.chains:
            await seed_initial_wallets(settings.seed_token_list, chain)
            break  # Seed once for primary chain (eth)


async def run_seed_from_moralis_discovery(chains: List[str]) -> int:
    """Use Moralis discovery API to find top wallets."""
    from clients.moralis import get_moralis_client

    client = get_moralis_client()
    count = 0

    for chain in chains:
        try:
            wallets_data = await client.get_discovery_wallets(chain, limit=20)
            if not wallets_data:
                continue
            for w in wallets_data:
                addr = w.get("address") or w.get("wallet_address")
                if addr:
                    await upsert_wallet(addr.lower(), chain)
                    count += 1
        except Exception as e:
            logger.warning("Moralis discovery failed for %s: %s", chain, e)

    if count:
        logger.info("Discovery: added %d wallets from Moralis", count)
    return count
