from __future__ import annotations

"""
Token/event analytics:
- Coordinated buy detection
- Pre-pump pattern detection
- New liquidity events
- Repeated buying in 24h window
- Earliest buyer identification
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select

from db.models import Trade, Wallet, WalletMetrics
from db.session import get_db
from utils.logger import get_logger

logger = get_logger(__name__)


async def detect_coordinated_buys(
    token_address: str,
    chain: str,
    window_hours: int = 4,
    min_wallets: int = 3,
    min_wallet_score: float = 60.0,
) -> Optional[Dict]:
    """
    Detect if multiple high-scored wallets bought the same token in a window.
    Returns event dict if detected.
    """
    since = datetime.utcnow() - timedelta(hours=window_hours)

    async with get_db() as db:
        result = await db.execute(
            select(Trade, Wallet, WalletMetrics)
            .join(Wallet, Trade.wallet_id == Wallet.id)
            .join(WalletMetrics, WalletMetrics.wallet_id == Wallet.id)
            .where(
                Trade.token_address == token_address,
                Trade.chain == chain,
                Trade.trade_type == "buy",
                Trade.timestamp >= since,
                Wallet.score >= min_wallet_score,
            )
            .order_by(Trade.timestamp)
        )
        rows = result.all()

    if not rows:
        return None

    # Deduplicate by wallet
    seen_wallets: Dict[str, Dict] = {}
    for trade, wallet, metrics in rows:
        if wallet.address not in seen_wallets:
            seen_wallets[wallet.address] = {
                "wallet_address": wallet.address,
                "score": wallet.score,
                "amount_usd": trade.amount_usd,
                "timestamp": trade.timestamp,
                "win_rate": metrics.win_rate if metrics else 0,
                "realized_pnl": metrics.realized_pnl_usd if metrics else 0,
            }
        else:
            seen_wallets[wallet.address]["amount_usd"] += trade.amount_usd

    if len(seen_wallets) < min_wallets:
        return None

    wallets_list = sorted(
        seen_wallets.values(), key=lambda x: x["score"], reverse=True
    )
    avg_score = sum(w["score"] for w in wallets_list) / len(wallets_list)
    total_volume = sum(w["amount_usd"] for w in wallets_list)

    return {
        "event_type": "COORDINATED_BUY",
        "token_address": token_address,
        "chain": chain,
        "wallet_count": len(wallets_list),
        "avg_score": avg_score,
        "total_volume_usd": total_volume,
        "wallets": wallets_list,
        "window_hours": window_hours,
        "detected_at": datetime.utcnow(),
    }


async def detect_pre_pump_pattern(
    token_address: str, chain: str, lookback_days: int = 90
) -> Optional[Dict]:
    """
    Check if wallets that bought this token historically before price pumps
    are doing so again now. Requires historical pre-pump accuracy data.
    """
    # Find wallets that bought this token recently (last 24h)
    since_recent = datetime.utcnow() - timedelta(hours=24)
    since_history = datetime.utcnow() - timedelta(days=lookback_days)

    async with get_db() as db:
        recent_result = await db.execute(
            select(Trade, Wallet)
            .join(Wallet, Trade.wallet_id == Wallet.id)
            .where(
                Trade.token_address == token_address,
                Trade.chain == chain,
                Trade.trade_type == "buy",
                Trade.timestamp >= since_recent,
            )
        )
        recent_rows = recent_result.all()

    if not recent_rows:
        return None

    pattern_wallets = []
    for trade, wallet in recent_rows:
        if wallet.score < 50:
            continue

        # Check if this wallet has pre-pump accuracy > 0.6
        async with get_db() as db:
            metrics_result = await db.execute(
                select(WalletMetrics).where(
                    WalletMetrics.wallet_id == wallet.id
                )
            )
            metrics = metrics_result.scalar_one_or_none()

        if metrics and metrics.pre_pump_accuracy >= 0.6 and metrics.trade_count >= 10:
            pattern_wallets.append({
                "wallet_address": wallet.address,
                "score": wallet.score,
                "pre_pump_accuracy": metrics.pre_pump_accuracy,
                "trade_count": metrics.trade_count,
                "realized_pnl": metrics.realized_pnl_usd,
            })

    if len(pattern_wallets) < 2:
        return None

    avg_accuracy = sum(w["pre_pump_accuracy"] for w in pattern_wallets) / len(pattern_wallets)

    return {
        "event_type": "PRE_PUMP_PATTERN",
        "token_address": token_address,
        "chain": chain,
        "pattern_wallet_count": len(pattern_wallets),
        "avg_pre_pump_accuracy": avg_accuracy,
        "wallets": pattern_wallets,
        "detected_at": datetime.utcnow(),
    }


async def detect_repeated_buying(
    token_address: str, chain: str, hours: int = 24
) -> Optional[Dict]:
    """
    Detect unusual repeated buying of a token in the last N hours.
    Returns event if unique buyer count is abnormally high.
    """
    since = datetime.utcnow() - timedelta(hours=hours)

    async with get_db() as db:
        result = await db.execute(
            select(
                Trade.wallet_id,
                func.count(Trade.id).label("buy_count"),
                func.sum(Trade.amount_usd).label("total_usd"),
            )
            .where(
                Trade.token_address == token_address,
                Trade.chain == chain,
                Trade.trade_type == "buy",
                Trade.timestamp >= since,
            )
            .group_by(Trade.wallet_id)
        )
        rows = result.all()

    if not rows or len(rows) < 5:
        return None

    total_volume = sum(r.total_usd or 0 for r in rows)
    unique_buyers = len(rows)

    return {
        "event_type": "REPEATED_BUYING",
        "token_address": token_address,
        "chain": chain,
        "unique_buyers_24h": unique_buyers,
        "total_volume_usd": total_volume,
        "detected_at": datetime.utcnow(),
    }


async def get_earliest_buyers(
    token_address: str, chain: str, top_n: int = 10
) -> List[Dict]:
    """Return wallets that bought a token earliest with their performance."""
    async with get_db() as db:
        result = await db.execute(
            select(Trade, Wallet)
            .join(Wallet, Trade.wallet_id == Wallet.id)
            .where(
                Trade.token_address == token_address,
                Trade.chain == chain,
                Trade.trade_type == "buy",
            )
            .order_by(Trade.timestamp)
            .limit(top_n * 3)
        )
        rows = result.all()

    # Deduplicate by wallet, keep earliest trade
    seen: Dict[str, Dict] = {}
    for trade, wallet in rows:
        if wallet.address not in seen:
            seen[wallet.address] = {
                "wallet_address": wallet.address,
                "score": wallet.score,
                "first_buy_at": trade.timestamp,
                "first_buy_price": trade.price_usd,
                "amount_usd": trade.amount_usd,
            }

    earliest = sorted(seen.values(), key=lambda x: x["first_buy_at"])[:top_n]
    return earliest


async def compute_token_event_score(event: Dict) -> float:
    """
    Score a detected token event 0-100.
    Considers wallet quality, coordination, volume, and pattern strength.
    """
    score = 0.0
    event_type = event.get("event_type", "")

    if event_type == "COORDINATED_BUY":
        avg_score = event.get("avg_score", 0)
        wallet_count = event.get("wallet_count", 0)
        volume = event.get("total_volume_usd", 0)

        score += min(avg_score, 100) * 0.4  # Wallet quality: up to 40pts
        score += min(wallet_count / 10.0, 1.0) * 20  # Count: up to 20pts
        score += min(volume / 100_000, 1.0) * 20  # Volume: up to 20pts
        score += 20  # Base for detection

    elif event_type == "PRE_PUMP_PATTERN":
        accuracy = event.get("avg_pre_pump_accuracy", 0)
        count = event.get("pattern_wallet_count", 0)

        score += accuracy * 50  # Accuracy: up to 50pts
        score += min(count / 5.0, 1.0) * 30  # Count: up to 30pts
        score += 20  # Base

    elif event_type == "NEW_LIQUIDITY":
        liquidity = event.get("total_liquidity_usd", 0)
        score += min(liquidity / 500_000, 1.0) * 60
        score += 40

    elif event_type == "REPEATED_BUYING":
        buyers = event.get("unique_buyers_24h", 0)
        volume = event.get("total_volume_usd", 0)
        score += min(buyers / 20, 1.0) * 50
        score += min(volume / 50_000, 1.0) * 50

    return min(score, 100.0)


async def find_tokens_with_activity(chain: str, hours: int = 4) -> List[str]:
    """Return token addresses with recent trading activity by tracked wallets."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with get_db() as db:
        result = await db.execute(
            select(Trade.token_address)
            .join(Wallet, Trade.wallet_id == Wallet.id)
            .where(
                Trade.chain == chain,
                Trade.timestamp >= since,
                Wallet.is_top100 == True,
            )
            .distinct()
        )
        return [row[0] for row in result.all()]
