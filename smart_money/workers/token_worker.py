from __future__ import annotations

"""
Token event worker.
Every 60s: scans active tokens for coordinated buys, new liquidity,
pre-pump patterns, cluster buys, and repeated buying.
"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Set

from sqlalchemy import select

from analytics.token_analytics import find_tokens_with_activity
from config.settings import get_settings
from db.models import Token, Trade
from db.session import get_db
from ingestion.token_fetcher import fetch_token_price, fetch_trending_tokens
from ingestion.token_fetcher import discover_wallets_from_token
from signals.detector import evaluate_token_signals
from utils.logger import get_logger

logger = get_logger(__name__)


async def scan_token(token_address: str, chain: str) -> int:
    """Evaluate all token-level signals. Returns signal count."""
    try:
        await fetch_token_price(token_address, chain)
        signals = await evaluate_token_signals(token_address, chain)
        return len(signals)
    except Exception as e:
        logger.debug("Token scan failed for %s: %s", token_address[:10], e)
        return 0


async def run_token_cycle(chains: List[str]) -> None:
    """One full token event scan cycle."""
    total_signals = 0

    for chain in chains:
        # Get active tokens from top wallet trades (last 4h)
        active_tokens = await find_tokens_with_activity(chain, hours=4)

        # Also pull trending tokens from discovery API
        try:
            trending = await fetch_trending_tokens(chain)
            for t in trending:
                addr = (
                    t.get("tokenAddress")
                    or t.get("address")
                    or t.get("token_address")
                )
                if addr:
                    active_tokens.append(addr.lower())
        except Exception as e:
            logger.debug("Trending token fetch failed for %s: %s", chain, e)

        # Deduplicate
        active_tokens = list(set(active_tokens))[:50]

        if not active_tokens:
            continue

        logger.info("Token cycle: scanning %d tokens on %s", len(active_tokens), chain)

        for token_addr in active_tokens:
            count = await scan_token(token_addr, chain)
            total_signals += count
            await asyncio.sleep(0.1)  # Small delay between tokens

    logger.info("Token cycle complete: %d signals fired", total_signals)


async def run_discovery_cycle(chains: List[str]) -> int:
    """
    Discover new wallets from trending tokens' top traders.
    Returns count of new wallets added.
    """
    from ingestion.wallet_fetcher import upsert_wallet
    from ingestion.token_fetcher import discover_wallets_from_token

    settings = get_settings()
    new_wallet_count = 0

    for chain in chains:
        trending = await fetch_trending_tokens(chain)
        if not trending:
            continue

        for token in trending[:5]:  # Top 5 trending tokens
            addr = (
                token.get("tokenAddress")
                or token.get("address")
                or token.get("token_address")
            )
            if not addr:
                continue

            try:
                wallet_pairs = await discover_wallets_from_token(addr, chain)
                for wallet_addr, wallet_chain in wallet_pairs:
                    # Only add if not already tracked
                    async with get_db() as db:
                        from db.models import Wallet
                        result = await db.execute(
                            select(Wallet).where(
                                Wallet.address == wallet_addr,
                                Wallet.chain == wallet_chain,
                            )
                        )
                        existing = result.scalar_one_or_none()

                    if existing is None:
                        await upsert_wallet(wallet_addr, wallet_chain)
                        new_wallet_count += 1

                await asyncio.sleep(0.5)  # Rate control between tokens
            except Exception as e:
                logger.debug("Wallet discovery failed for %s: %s", addr[:10], e)

    if new_wallet_count:
        logger.info("Discovery cycle: added %d new wallets", new_wallet_count)
    return new_wallet_count
