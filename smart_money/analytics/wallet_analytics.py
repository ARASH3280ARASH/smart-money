from __future__ import annotations

"""
Computes per-wallet analytics from stored trade history:
- Realized/unrealized PnL, ROI
- Win rate, trade count
- Average holding time
- Early entry detection (bought in first 5-10% of move)
- Smart exit detection (sold within 20% of local peak)
- Pre-pump accuracy
"""

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select

from db.models import Trade, Wallet, WalletMetrics
from db.session import get_db
from utils.logger import get_logger

logger = get_logger(__name__)


async def compute_wallet_metrics(wallet_id: int) -> Optional[WalletMetrics]:
    """Full analytics recompute from trade history for a single wallet."""
    async with get_db() as db:
        result = await db.execute(
            select(Trade)
            .where(Trade.wallet_id == wallet_id)
            .order_by(Trade.timestamp)
        )
        trades: List[Trade] = result.scalars().all()

    if not trades:
        return None

    buys = [t for t in trades if t.trade_type == "buy"]
    sells = [t for t in trades if t.trade_type == "sell"]

    # Per-token P&L matching (FIFO)
    pnl_result = _compute_pnl(trades)
    realized_pnl = pnl_result["realized_pnl"]
    win_count = pnl_result["win_count"]
    completed_trades = pnl_result["completed_trades"]
    avg_roi = pnl_result["avg_roi"]

    win_rate = win_count / completed_trades if completed_trades > 0 else 0.0
    total_volume = sum(t.amount_usd for t in trades)
    trade_count = len(trades)
    avg_trade_size = total_volume / trade_count if trade_count else 0.0

    avg_holding = _compute_avg_holding_time(trades)
    early_entry_count = pnl_result["early_entry_count"]
    smart_exit_count = pnl_result["smart_exit_count"]
    pre_pump_accuracy = pnl_result["pre_pump_accuracy"]
    last_trade_at = trades[-1].timestamp if trades else None

    async with get_db() as db:
        result = await db.execute(
            select(WalletMetrics).where(WalletMetrics.wallet_id == wallet_id)
        )
        metrics = result.scalar_one_or_none()
        if metrics is None:
            metrics = WalletMetrics(wallet_id=wallet_id)
            db.add(metrics)

        metrics.realized_pnl_usd = realized_pnl
        metrics.roi_pct = avg_roi
        metrics.win_rate = win_rate
        metrics.win_count = win_count
        metrics.trade_count = trade_count
        metrics.total_volume_usd = total_volume
        metrics.avg_trade_size_usd = avg_trade_size
        metrics.avg_holding_hours = avg_holding
        metrics.early_entry_count = early_entry_count
        metrics.smart_exit_count = smart_exit_count
        metrics.pre_pump_accuracy = pre_pump_accuracy
        metrics.last_trade_at = last_trade_at
        metrics.updated_at = datetime.utcnow()

    return metrics


def _compute_pnl(trades: List[Trade]) -> Dict[str, Any]:
    """
    FIFO PnL matching per token.
    Returns realized PnL, win/loss counts, early entry and smart exit counts.
    """
    # Group by token
    by_token: Dict[str, List[Trade]] = {}
    for t in trades:
        by_token.setdefault(t.token_address, []).append(t)

    total_realized = 0.0
    win_count = 0
    completed_trades = 0
    total_roi = 0.0
    early_entry_count = 0
    smart_exit_count = 0

    for token_addr, token_trades in by_token.items():
        token_trades.sort(key=lambda t: t.timestamp)
        buy_queue: List[Trade] = []

        for trade in token_trades:
            if trade.trade_type == "buy":
                buy_queue.append(trade)
                if trade.is_early_entry:
                    early_entry_count += 1
            elif trade.trade_type == "sell" and buy_queue:
                buy = buy_queue.pop(0)  # FIFO
                if buy.price_usd > 0 and trade.price_usd > 0:
                    roi = (trade.price_usd - buy.price_usd) / buy.price_usd
                    pnl = (trade.price_usd - buy.price_usd) * min(
                        trade.token_amount, buy.token_amount
                    )
                    total_realized += pnl
                    total_roi += roi
                    completed_trades += 1
                    if pnl > 0:
                        win_count += 1
                    if trade.is_smart_exit:
                        smart_exit_count += 1

    avg_roi = (total_roi / completed_trades * 100) if completed_trades > 0 else 0.0
    pre_pump_accuracy = win_count / completed_trades if completed_trades > 0 else 0.0

    return {
        "realized_pnl": total_realized,
        "win_count": win_count,
        "completed_trades": completed_trades,
        "avg_roi": avg_roi,
        "early_entry_count": early_entry_count,
        "smart_exit_count": smart_exit_count,
        "pre_pump_accuracy": pre_pump_accuracy,
    }


def _compute_avg_holding_time(trades: List[Trade]) -> float:
    """Return average holding time in hours using FIFO matching."""
    by_token: Dict[str, List[Trade]] = {}
    for t in trades:
        by_token.setdefault(t.token_address, []).append(t)

    holding_times: List[float] = []
    for token_addr, token_trades in by_token.items():
        token_trades.sort(key=lambda t: t.timestamp)
        buy_queue: List[Trade] = []
        for trade in token_trades:
            if trade.trade_type == "buy":
                buy_queue.append(trade)
            elif trade.trade_type == "sell" and buy_queue:
                buy = buy_queue.pop(0)
                delta = (trade.timestamp - buy.timestamp).total_seconds() / 3600
                holding_times.append(max(delta, 0))

    if not holding_times:
        return 0.0
    return sum(holding_times) / len(holding_times)


async def mark_early_entries(token_address: str, chain: str) -> int:
    """
    For a given token, identify which buy trades were 'early entries'
    (bought within the first 10% of total price appreciation window).
    Marks Trade.is_early_entry = True. Returns count marked.
    """
    async with get_db() as db:
        result = await db.execute(
            select(Trade)
            .where(
                Trade.token_address == token_address,
                Trade.chain == chain,
                Trade.trade_type == "buy",
                Trade.price_usd > 0,
            )
            .order_by(Trade.timestamp)
        )
        buy_trades: List[Trade] = result.scalars().all()

    if len(buy_trades) < 3:
        return 0

    prices = [t.price_usd for t in buy_trades]
    min_price = min(prices)
    max_price = max(prices)
    if max_price <= min_price:
        return 0

    price_range = max_price - min_price
    threshold = min_price + price_range * 0.10  # first 10% of move

    marked = 0
    async with get_db() as db:
        for trade in buy_trades:
            if trade.price_usd <= threshold and not trade.is_early_entry:
                result = await db.execute(
                    select(Trade).where(Trade.id == trade.id)
                )
                t = result.scalar_one_or_none()
                if t:
                    t.is_early_entry = True
                    marked += 1

    return marked


async def mark_smart_exits(token_address: str, chain: str) -> int:
    """
    Mark sell trades as 'smart exits' if they occurred within 20% of
    the local price peak for that token.
    """
    async with get_db() as db:
        result = await db.execute(
            select(Trade)
            .where(
                Trade.token_address == token_address,
                Trade.chain == chain,
                Trade.trade_type == "sell",
                Trade.price_usd > 0,
            )
            .order_by(Trade.timestamp)
        )
        sell_trades: List[Trade] = result.scalars().all()

        result2 = await db.execute(
            select(Trade)
            .where(
                Trade.token_address == token_address,
                Trade.chain == chain,
                Trade.price_usd > 0,
            )
            .order_by(Trade.timestamp)
        )
        all_trades: List[Trade] = result2.scalars().all()

    if not all_trades or not sell_trades:
        return 0

    max_price = max(t.price_usd for t in all_trades)
    smart_threshold = max_price * 0.80  # within 20% of peak

    marked = 0
    async with get_db() as db:
        for sell in sell_trades:
            if sell.price_usd >= smart_threshold and not sell.is_smart_exit:
                result = await db.execute(
                    select(Trade).where(Trade.id == sell.id)
                )
                t = result.scalar_one_or_none()
                if t:
                    t.is_smart_exit = True
                    marked += 1

    return marked


async def get_wallet_portfolio_composition(wallet_id: int) -> List[Dict]:
    """Returns recent token holdings from trade history."""
    async with get_db() as db:
        result = await db.execute(
            select(Trade)
            .where(Trade.wallet_id == wallet_id)
            .order_by(Trade.timestamp.desc())
        )
        trades = result.scalars().all()

    # Net token balances
    holdings: Dict[str, Dict] = {}
    for t in trades:
        key = f"{t.token_address}_{t.chain}"
        if key not in holdings:
            holdings[key] = {
                "token_address": t.token_address,
                "token_symbol": t.token_symbol,
                "chain": t.chain,
                "net_amount": 0.0,
                "cost_basis_usd": 0.0,
            }
        if t.trade_type == "buy":
            holdings[key]["net_amount"] += t.token_amount
            holdings[key]["cost_basis_usd"] += t.amount_usd
        else:
            holdings[key]["net_amount"] -= t.token_amount
            holdings[key]["cost_basis_usd"] -= t.amount_usd

    return [v for v in holdings.values() if v["net_amount"] > 0]


async def get_wallet_sector_specialization(wallet_id: int) -> Dict[str, int]:
    """Returns token trade frequency map for sector detection (simplified)."""
    async with get_db() as db:
        result = await db.execute(
            select(Trade.token_symbol, func.count(Trade.id).label("count"))
            .where(Trade.wallet_id == wallet_id)
            .group_by(Trade.token_symbol)
            .order_by(func.count(Trade.id).desc())
        )
        rows = result.all()

    return {row.token_symbol: row.count for row in rows[:20]}
