from __future__ import annotations

"""
Continuous ingestion worker.
Every 30s: scans top-100 wallets for new transactions and fires trade signals.
"""

import asyncio
from datetime import datetime
from typing import List

from sqlalchemy import select

from alerts.telegram_alert import deliver_pending_signals
from analytics.scoring import update_wallet_score
from analytics.wallet_analytics import compute_wallet_metrics, mark_early_entries, mark_smart_exits
from db.models import Trade, Wallet
from db.session import get_db
from ingestion.wallet_fetcher import fetch_and_store_wallet_history
from signals.detector import evaluate_trade_signals
from utils.logger import get_logger
from utils.rate_limiter import get_rate_limiter

logger = get_logger(__name__)


async def scan_wallet(wallet: Wallet) -> int:
    """Scan a single wallet for new trades and evaluate signals. Returns new trade count."""
    try:
        new_count = await fetch_and_store_wallet_history(wallet, days_back=3)
        if new_count > 0:
            logger.info(
                "Wallet %s on %s: %d new trades",
                wallet.address[:10], wallet.chain, new_count,
            )
            # Recompute metrics and score after new trades
            await compute_wallet_metrics(wallet.id)
            await update_wallet_score(wallet.id)

            # Get new trades and evaluate signals
            async with get_db() as db:
                result = await db.execute(
                    select(Trade)
                    .where(Trade.wallet_id == wallet.id)
                    .order_by(Trade.timestamp.desc())
                    .limit(new_count)
                )
                recent_trades: List[Trade] = result.scalars().all()

            for trade in recent_trades:
                await evaluate_trade_signals(wallet, trade)

        return new_count
    except Exception as e:
        logger.error("Error scanning wallet %s: %s", wallet.address[:10], e)
        return 0


async def run_ingestion_cycle() -> None:
    """One full ingestion cycle across all top-100 wallets."""
    async with get_db() as db:
        result = await db.execute(
            select(Wallet)
            .where(Wallet.is_top100 == True, Wallet.is_active == True)
            .order_by(Wallet.last_synced.asc().nullsfirst())
        )
        wallets: List[Wallet] = result.scalars().all()

    if not wallets:
        logger.debug("No top-100 wallets to scan yet")
        return

    logger.info("Ingestion cycle: scanning %d wallets", len(wallets))
    total_new = 0

    # Scan in batches to control CU pressure
    batch_size = 10
    for i in range(0, len(wallets), batch_size):
        batch = wallets[i : i + batch_size]
        tasks = [scan_wallet(w) for w in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, int):
                total_new += r

        # Adaptive sleep based on rate limiter pressure
        limiter = get_rate_limiter()
        pressure = await limiter.current_pressure()
        if pressure > 0.7:
            await asyncio.sleep(2.0)
        elif pressure > 0.5:
            await asyncio.sleep(0.5)

    # Deliver any pending signals
    sent = await deliver_pending_signals()

    logger.info(
        "Ingestion cycle complete: %d new trades, %d signals sent", total_new, sent
    )


async def run_mark_labels_cycle(chain: str) -> None:
    """Periodically mark early entries and smart exits on active tokens."""
    async with get_db() as db:
        result = await db.execute(
            select(Trade.token_address).distinct()
            .join(Wallet, Trade.wallet_id == Wallet.id)
            .where(
                Trade.chain == chain,
                Wallet.is_top100 == True,
            )
        )
        token_addresses = [row[0] for row in result.all()]

    for token_addr in token_addresses[:50]:  # Cap per cycle
        try:
            await mark_early_entries(token_addr, chain)
            await mark_smart_exits(token_addr, chain)
        except Exception as e:
            logger.debug("Label marking failed for %s: %s", token_addr[:10], e)
