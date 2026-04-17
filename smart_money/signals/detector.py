from __future__ import annotations

"""
Signal detection engine.
Evaluates all 8 signal types and creates Signal records.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from analytics.scoring import compute_score
from analytics.token_analytics import (
    compute_token_event_score,
    detect_coordinated_buys,
    detect_pre_pump_pattern,
    detect_repeated_buying,
)
from config.known_wallets import get_label
from config.settings import get_settings
from db.models import Signal, Trade, Wallet, WalletMetrics
from db.session import get_db
from graph.clustering import detect_cluster_buys
from ingestion.token_fetcher import detect_new_liquidity
from utils.logger import get_logger

logger = get_logger(__name__)


def _wallet_display(address: str) -> str:
    """Return 'Label (0xabc...def)' if known, else '0xabc...def'."""
    short = f"{address[:6]}...{address[-4:]}" if len(address) > 10 else address
    label = get_label(address)
    return f"{label} ({short})" if label else short


def _confidence(score: float) -> str:
    if score >= 80:
        return "HIGH"
    if score >= 55:
        return "MEDIUM"
    return "LOW"


async def _signal_exists_recently(
    signal_type: str,
    token_address: Optional[str],
    chain: str,
    hours: int = 1,
) -> bool:
    """Dedup: check if same signal type fired for same token in last N hours."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with get_db() as db:
        query = select(Signal).where(
            Signal.signal_type == signal_type,
            Signal.chain == chain,
            Signal.triggered_at >= since,
        )
        if token_address:
            query = query.where(Signal.token_address == token_address)
        result = await db.execute(query)
        return result.scalar_one_or_none() is not None


async def _create_signal(
    signal_type: str,
    token_address: Optional[str],
    token_symbol: Optional[str],
    chain: str,
    wallets: List[str],
    score: float,
    summary: str,
    evidence: str,
) -> Optional[Signal]:
    """Create and persist a Signal record if not a recent duplicate."""
    dedup_window = 2 if signal_type in ("SMART_WALLET_BUY", "WHALE_MOVE") else 1
    if await _signal_exists_recently(signal_type, token_address, chain, hours=dedup_window):
        return None

    sig = Signal(
        signal_type=signal_type,
        token_address=token_address,
        token_symbol=token_symbol,
        chain=chain,
        wallets_json=json.dumps(wallets),
        score=score,
        confidence=_confidence(score),
        summary=summary,
        evidence=evidence,
        triggered_at=datetime.utcnow(),
        sent_telegram=False,
    )
    async with get_db() as db:
        db.add(sig)
        await db.flush()

    logger.info(
        "Signal created: %s | %s | score=%.1f | chain=%s",
        signal_type, token_symbol or token_address, score, chain
    )
    return sig


# ── Individual signal detectors ────────────────────────────────────────────


async def check_smart_wallet_buy(
    wallet: Wallet, trade: Trade
) -> Optional[Signal]:
    """Fire when a high-score wallet buys a token."""
    settings = get_settings()
    if wallet.score < settings.smart_wallet_score_threshold:
        return None
    if trade.trade_type != "buy":
        return None
    if trade.amount_usd < 1000:  # Ignore dust trades
        return None

    async with get_db() as db:
        result = await db.execute(
            select(WalletMetrics).where(WalletMetrics.wallet_id == wallet.id)
        )
        metrics = result.scalar_one_or_none()

    score = wallet.score
    display = _wallet_display(wallet.address)
    win_rate_str = f"{metrics.win_rate*100:.0f}% win rate" if metrics else ""
    pnl_str = f"${metrics.realized_pnl_usd:,.0f} realized PnL" if metrics else ""

    return await _create_signal(
        signal_type="SMART_WALLET_BUY",
        token_address=trade.token_address,
        token_symbol=trade.token_symbol,
        chain=trade.chain,
        wallets=[wallet.address],
        score=score,
        summary=f"{display} (score {score:.0f}) bought ${trade.token_symbol} for ${trade.amount_usd:,.0f}",
        evidence=f"{win_rate_str}, {pnl_str}".strip(", "),
    )


async def check_coordinated_buy(token_address: str, chain: str) -> Optional[Signal]:
    """Fire when 3+ smart wallets buy same token within window."""
    settings = get_settings()
    event = await detect_coordinated_buys(
        token_address,
        chain,
        window_hours=settings.coordinated_buy_window_hours,
        min_wallets=settings.coordinated_buy_min_wallets,
    )
    if not event:
        return None

    score = await compute_token_event_score(event)
    wallets = [w["wallet_address"] for w in event["wallets"]]
    avg_score = event["avg_score"]
    count = event["wallet_count"]
    volume = event["total_volume_usd"]

    token_symbol = await _get_token_symbol(token_address, chain)
    evidence_parts = [
        f"{_wallet_display(w['wallet_address'])} score={w['score']:.0f} pnl=${w['realized_pnl']:,.0f}"
        for w in event["wallets"][:3]
    ]

    return await _create_signal(
        signal_type="COORDINATED_BUY",
        token_address=token_address,
        token_symbol=token_symbol,
        chain=chain,
        wallets=wallets,
        score=score,
        summary=f"{count} smart wallets (avg score {avg_score:.0f}) bought within {event['window_hours']}h, total ${volume:,.0f}",
        evidence=" | ".join(evidence_parts),
    )


async def check_pre_pump_pattern(token_address: str, chain: str) -> Optional[Signal]:
    """Fire when wallets with pre-pump history buy again."""
    event = await detect_pre_pump_pattern(token_address, chain)
    if not event:
        return None

    score = await compute_token_event_score(event)
    wallets = [w["wallet_address"] for w in event["wallets"]]
    accuracy = event["avg_pre_pump_accuracy"]
    count = event["pattern_wallet_count"]
    token_symbol = await _get_token_symbol(token_address, chain)

    return await _create_signal(
        signal_type="PRE_PUMP_PATTERN",
        token_address=token_address,
        token_symbol=token_symbol,
        chain=chain,
        wallets=wallets,
        score=score,
        summary=f"{count} wallets with {accuracy*100:.0f}% pre-pump accuracy bought ${token_symbol}",
        evidence=f"Avg accuracy: {accuracy*100:.0f}% | {count} wallets with 10+ trades confirmed history",
    )


async def check_new_liquidity(token_address: str, chain: str) -> Optional[Signal]:
    """Fire when significant new DEX liquidity is detected."""
    settings = get_settings()
    event = await detect_new_liquidity(
        token_address, chain, min_usd=settings.whale_move_min_usd
    )
    if not event:
        return None

    score = await compute_token_event_score(event)
    liquidity = event["total_liquidity_usd"]
    new_pairs = event["new_pair_count"]
    token_symbol = await _get_token_symbol(token_address, chain)

    return await _create_signal(
        signal_type="NEW_LIQUIDITY",
        token_address=token_address,
        token_symbol=token_symbol,
        chain=chain,
        wallets=[],
        score=score,
        summary=f"${liquidity:,.0f} new liquidity on {new_pairs} new DEX pair(s) for ${token_symbol}",
        evidence=f"{new_pairs} pair(s) created in last 24h | total pool: ${liquidity:,.0f}",
    )


async def check_cluster_buy(token_address: str, chain: str) -> Optional[Signal]:
    """Fire when a wallet cluster buys together."""
    event = await detect_cluster_buys(token_address, chain)
    if not event:
        return None

    count = event["cluster_buyer_count"]
    cluster_id = event["cluster_id"]
    token_symbol = await _get_token_symbol(token_address, chain)
    score = min(40 + count * 10, 90)

    return await _create_signal(
        signal_type="CLUSTER_BUY",
        token_address=token_address,
        token_symbol=token_symbol,
        chain=chain,
        wallets=event["cluster_buyers"],
        score=float(score),
        summary=f"Wallet cluster #{cluster_id} ({count} members) buying ${token_symbol} together",
        evidence=f"Cluster ID {cluster_id} | {count} co-trading wallets detected buying within 4h",
    )


async def check_smart_exit(
    wallet: Wallet, trade: Trade
) -> Optional[Signal]:
    """Fire when a high-score wallet sells (potential dump signal)."""
    settings = get_settings()
    if wallet.score < settings.smart_wallet_score_threshold:
        return None
    if trade.trade_type != "sell":
        return None
    if trade.amount_usd < 5000:
        return None
    if not trade.is_smart_exit:
        return None  # Only flag confirmed smart exits

    token_symbol = trade.token_symbol or await _get_token_symbol(
        trade.token_address, trade.chain
    )

    return await _create_signal(
        signal_type="SMART_EXIT",
        token_address=trade.token_address,
        token_symbol=token_symbol,
        chain=trade.chain,
        wallets=[wallet.address],
        score=wallet.score * 0.9,
        summary=f"⚠️ Smart wallet (score {wallet.score:.0f}) exiting ${token_symbol} — ${trade.amount_usd:,.0f}",
        evidence=f"Sell near local peak | Wallet has {wallet.score:.0f}/100 score",
    )


async def check_whale_move(wallet: Wallet, trade: Trade) -> Optional[Signal]:
    """Fire when a tracked wallet moves large capital."""
    settings = get_settings()
    if trade.amount_usd < settings.whale_move_min_usd:
        return None
    if wallet.score < 50:
        return None

    token_symbol = trade.token_symbol or await _get_token_symbol(
        trade.token_address, trade.chain
    )
    direction = "buying" if trade.trade_type == "buy" else "selling"
    score = min(50 + wallet.score * 0.4, 95)

    return await _create_signal(
        signal_type="WHALE_MOVE",
        token_address=trade.token_address,
        token_symbol=token_symbol,
        chain=trade.chain,
        wallets=[wallet.address],
        score=score,
        summary=f"Whale (score {wallet.score:.0f}) {direction} ${token_symbol} — ${trade.amount_usd:,.0f}",
        evidence=f"Capital move: ${trade.amount_usd:,.0f} | Wallet score: {wallet.score:.0f}/100",
    )


async def check_early_entry(wallet: Wallet, trade: Trade) -> Optional[Signal]:
    """Fire when a tracked wallet makes an early entry into a token move."""
    if not trade.is_early_entry:
        return None
    if wallet.score < 60:
        return None

    token_symbol = trade.token_symbol or await _get_token_symbol(
        trade.token_address, trade.chain
    )
    score = min(wallet.score * 0.95, 98)

    return await _create_signal(
        signal_type="EARLY_ENTRY",
        token_address=trade.token_address,
        token_symbol=token_symbol,
        chain=trade.chain,
        wallets=[wallet.address],
        score=score,
        summary=f"Smart wallet entered ${token_symbol} in first 10% of price move",
        evidence=f"Wallet score {wallet.score:.0f} | Buy price: ${trade.price_usd:.6f} | Historical early entry track record",
    )


# ── Helpers ────────────────────────────────────────────────────────────────


async def _get_token_symbol(token_address: str, chain: str) -> str:
    from db.models import Token
    async with get_db() as db:
        result = await db.execute(
            select(Token).where(
                Token.address == token_address, Token.chain == chain
            )
        )
        token = result.scalar_one_or_none()
    return token.symbol if token and token.symbol else token_address[:8] + "..."


async def evaluate_trade_signals(wallet: Wallet, trade: Trade) -> List[Signal]:
    """Evaluate all trade-based signals for a single trade event."""
    signals = []
    for checker in [check_smart_wallet_buy, check_smart_exit, check_whale_move, check_early_entry]:
        try:
            sig = await checker(wallet, trade)
            if sig:
                signals.append(sig)
        except Exception as e:
            logger.warning("Signal check %s failed: %s", checker.__name__, e)
    return signals


async def evaluate_token_signals(token_address: str, chain: str) -> List[Signal]:
    """Evaluate all token-level signals."""
    signals = []
    for checker in [
        check_coordinated_buy,
        check_pre_pump_pattern,
        check_new_liquidity,
        check_cluster_buy,
    ]:
        try:
            sig = await checker(token_address, chain)
            if sig:
                signals.append(sig)
        except Exception as e:
            logger.warning(
                "Token signal check %s failed for %s: %s",
                checker.__name__, token_address[:10], e,
            )
    return signals
