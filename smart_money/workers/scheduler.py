from __future__ import annotations

"""
APScheduler job registry.
Registers all background workers with their cadences.
"""

import asyncio
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def _safe_job(func: Callable, *args, **kwargs):
    """Wrap a coroutine job with error isolation."""
    async def _wrapper():
        try:
            await func(*args, **kwargs)
        except Exception as e:
            logger.error("Scheduled job %s failed: %s", func.__name__, e, exc_info=True)
    return _wrapper


async def setup_and_start_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = get_scheduler()

    if scheduler.running:
        return scheduler

    chains = settings.chains

    # ── Job 1: Wallet ingestion (every 30s) ────────────────────────────
    from workers.ingestion_worker import run_ingestion_cycle

    scheduler.add_job(
        _safe_job(run_ingestion_cycle),
        trigger=IntervalTrigger(seconds=settings.scan_interval_wallets),
        id="ingestion_cycle",
        name="Wallet Ingestion",
        replace_existing=True,
        max_instances=1,
    )

    # ── Job 2: Token event scanning (every 60s) ─────────────────────────
    from workers.token_worker import run_token_cycle

    scheduler.add_job(
        _safe_job(run_token_cycle, chains),
        trigger=IntervalTrigger(seconds=settings.scan_interval_tokens),
        id="token_cycle",
        name="Token Event Scan",
        replace_existing=True,
        max_instances=1,
    )

    # ── Job 3: Score update (every 5 min) ───────────────────────────────
    from analytics.scoring import get_top_wallets
    from analytics.wallet_analytics import compute_wallet_metrics
    from analytics.scoring import batch_update_scores

    async def _score_update_job():
        wallets = await get_top_wallets(limit=200)
        ids = [w.id for w in wallets]
        await batch_update_scores(ids)

    scheduler.add_job(
        _safe_job(_score_update_job),
        trigger=IntervalTrigger(seconds=settings.score_update_interval),
        id="score_update",
        name="Score Update",
        replace_existing=True,
        max_instances=1,
    )

    # ── Job 4: Top-100 sync (every 10 min) ──────────────────────────────
    from workers.top100_sync import run_top100_sync

    scheduler.add_job(
        _safe_job(run_top100_sync),
        trigger=IntervalTrigger(seconds=settings.top100_sync_interval),
        id="top100_sync",
        name="Top-100 Sync",
        replace_existing=True,
        max_instances=1,
    )

    # ── Job 5: Graph clustering (every 30 min) ──────────────────────────
    from graph.clustering import run_clustering_pipeline

    async def _graph_job():
        for chain in chains:
            await run_clustering_pipeline(chain)

    scheduler.add_job(
        _safe_job(_graph_job),
        trigger=IntervalTrigger(seconds=settings.graph_update_interval),
        id="graph_update",
        name="Graph Clustering",
        replace_existing=True,
        max_instances=1,
    )

    # ── Job 6: Wallet discovery (every 20 min) ───────────────────────────
    from workers.token_worker import run_discovery_cycle

    scheduler.add_job(
        _safe_job(run_discovery_cycle, chains),
        trigger=IntervalTrigger(minutes=20),
        id="discovery_cycle",
        name="Wallet Discovery",
        replace_existing=True,
        max_instances=1,
    )

    # ── Job 7: Periodic label marking (every 15 min) ─────────────────────
    from workers.ingestion_worker import run_mark_labels_cycle

    async def _label_job():
        for chain in chains:
            await run_mark_labels_cycle(chain)

    scheduler.add_job(
        _safe_job(_label_job),
        trigger=IntervalTrigger(minutes=15),
        id="label_marking",
        name="Label Marking",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info(
        "Scheduler started with %d jobs: %s",
        len(scheduler.get_jobs()),
        [j.name for j in scheduler.get_jobs()],
    )
    return scheduler


async def stop_scheduler() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
