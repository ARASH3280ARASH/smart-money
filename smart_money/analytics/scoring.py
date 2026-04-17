from __future__ import annotations

"""
Smart wallet scoring engine.
Produces a 0-100 composite score weighting 8 factors.
"""

import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select

from db.models import Trade, Wallet, WalletMetrics, WalletRelationship
from db.session import get_db
from utils.logger import get_logger

logger = get_logger(__name__)

# Factor weights (must sum to 1.0)
WEIGHTS = {
    "win_rate": 0.20,
    "pnl_quality": 0.20,
    "roi_consistency": 0.15,
    "early_entry": 0.20,
    "smart_exit": 0.10,
    "coordination": 0.05,
    "capital_size": 0.05,
    "recency": 0.05,
}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _log_scale(value: float, lo: float, hi: float) -> float:
    """Map value logarithmically to 0-1 between lo and hi."""
    if value <= lo:
        return 0.0
    if value >= hi:
        return 1.0
    return math.log(value - lo + 1) / math.log(hi - lo + 1)


def score_win_rate(win_rate: float, trade_count: int) -> float:
    """
    Win rate score. Requires minimum trades to be meaningful.
    Target: >65% win rate = full score.
    """
    if trade_count < 5:
        return win_rate * 0.5  # Penalize low sample
    # Scale: 0% → 0, 65%+ → 1.0
    return _clamp(win_rate / 0.65)


def score_pnl_quality(realized_pnl_usd: float) -> float:
    """
    PnL quality: log-scaled from $1k to $500k.
    """
    return _clamp(_log_scale(max(realized_pnl_usd, 0), 1_000, 500_000))


def score_roi_consistency(avg_roi_pct: float) -> float:
    """
    ROI consistency: 20%+ average ROI per completed trade = full score.
    """
    if avg_roi_pct <= 0:
        return 0.0
    return _clamp(avg_roi_pct / 20.0)


def score_early_entry(early_entry_count: int, trade_count: int) -> float:
    """
    Early entry rate: fraction of trades that were early entries.
    """
    if trade_count == 0:
        return 0.0
    rate = early_entry_count / trade_count
    return _clamp(rate / 0.3)  # 30% early entry rate = full score


def score_smart_exit(smart_exit_count: int, sell_count: int) -> float:
    """
    Smart exit rate: fraction of sell trades that were smart exits.
    """
    if sell_count == 0:
        return 0.0
    rate = smart_exit_count / sell_count
    return _clamp(rate / 0.4)  # 40% smart exit rate = full score


def score_coordination(
    wallet_address: str, chain: str, high_score_co_traders: int
) -> float:
    """
    Coordination bonus: how often this wallet trades with other high-scorers.
    """
    if high_score_co_traders == 0:
        return 0.0
    return _clamp(high_score_co_traders / 5.0)  # 5+ co-traders = full score


def score_capital_size(avg_trade_size_usd: float) -> float:
    """
    Capital size: meaningful trade sizes indicate real money.
    $10k → 0.5, $100k → 1.0
    """
    return _clamp(_log_scale(avg_trade_size_usd, 1_000, 100_000))


def score_recency(last_trade_at: Optional[datetime]) -> float:
    """
    Recency: active in last 30 days = full score.
    """
    if not last_trade_at:
        return 0.0
    days_ago = (datetime.utcnow() - last_trade_at).days
    if days_ago <= 7:
        return 1.0
    if days_ago <= 30:
        return 0.7
    if days_ago <= 90:
        return 0.3
    return 0.0


def compute_score(
    metrics: WalletMetrics,
    high_score_co_traders: int = 0,
) -> Tuple[float, Dict[str, float]]:
    """
    Compute composite wallet score 0-100.
    Returns (score, factor_breakdown).
    """
    sell_count = max(metrics.trade_count - metrics.win_count, 0)

    factors = {
        "win_rate": score_win_rate(metrics.win_rate, metrics.trade_count),
        "pnl_quality": score_pnl_quality(metrics.realized_pnl_usd),
        "roi_consistency": score_roi_consistency(metrics.roi_pct),
        "early_entry": score_early_entry(
            metrics.early_entry_count, metrics.trade_count
        ),
        "smart_exit": score_smart_exit(metrics.smart_exit_count, sell_count),
        "coordination": score_coordination("", "", high_score_co_traders),
        "capital_size": score_capital_size(metrics.avg_trade_size_usd),
        "recency": score_recency(metrics.last_trade_at),
    }

    raw_score = sum(WEIGHTS[k] * v for k, v in factors.items())
    final_score = round(_clamp(raw_score) * 100, 2)

    return final_score, factors


async def update_wallet_score(wallet_id: int) -> float:
    """Fetch metrics, compute score, persist to wallet. Returns new score."""
    async with get_db() as db:
        result = await db.execute(
            select(WalletMetrics).where(WalletMetrics.wallet_id == wallet_id)
        )
        metrics = result.scalar_one_or_none()

    if metrics is None:
        return 0.0

    # Count high-score co-traders
    async with get_db() as db:
        result = await db.execute(
            select(Wallet)
            .where(Wallet.id == wallet_id)
        )
        wallet = result.scalar_one_or_none()

    high_score_co_traders = 0
    if wallet:
        async with get_db() as db:
            result = await db.execute(
                select(WalletRelationship)
                .where(
                    WalletRelationship.wallet_a == wallet.address,
                    WalletRelationship.chain == wallet.chain,
                    WalletRelationship.relationship_score >= 60,
                )
            )
            high_score_co_traders = len(result.scalars().all())

    score, _ = compute_score(metrics, high_score_co_traders)

    async with get_db() as db:
        result = await db.execute(
            select(Wallet).where(Wallet.id == wallet_id)
        )
        w = result.scalar_one_or_none()
        if w:
            w.score = score

    return score


async def batch_update_scores(wallet_ids: List[int]) -> Dict[int, float]:
    """Update scores for a list of wallet IDs. Returns {wallet_id: score}."""
    results = {}
    for wid in wallet_ids:
        try:
            score = await update_wallet_score(wid)
            results[wid] = score
        except Exception as e:
            logger.warning("Score update failed for wallet %d: %s", wid, e)
            results[wid] = 0.0
    return results


async def get_top_wallets(
    limit: int = 100, chain: Optional[str] = None
) -> List[Wallet]:
    """Return top wallets by score."""
    async with get_db() as db:
        query = select(Wallet).where(Wallet.is_active == True).order_by(
            Wallet.score.desc()
        )
        if chain:
            query = query.where(Wallet.chain == chain)
        query = query.limit(limit)
        result = await db.execute(query)
        return result.scalars().all()


async def rank_and_flag_top100(top_n: int = 100) -> List[Wallet]:
    """Recompute ranks, flag top-N wallets, return them."""
    top_wallets = await get_top_wallets(limit=top_n * 2)

    async with get_db() as db:
        # Clear old top100 flags
        all_result = await db.execute(
            select(Wallet).where(Wallet.is_top100 == True)
        )
        for w in all_result.scalars().all():
            w.is_top100 = False
            w.rank = None

    # Set new top100
    async with get_db() as db:
        for rank, wallet in enumerate(top_wallets[:top_n], start=1):
            result = await db.execute(
                select(Wallet).where(Wallet.id == wallet.id)
            )
            w = result.scalar_one_or_none()
            if w:
                w.is_top100 = True
                w.rank = rank

    logger.info("Ranked and flagged top %d wallets", min(top_n, len(top_wallets)))
    return top_wallets[:top_n]
