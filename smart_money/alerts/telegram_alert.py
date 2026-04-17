from __future__ import annotations

"""
Signal → Telegram alert formatter and delivery.
"""

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from clients.telegram_client import get_telegram_client
from config.chains import get_explorer_address_url, get_explorer_tx_url
from db.models import Signal
from db.session import get_db
from utils.logger import get_logger

logger = get_logger(__name__)

CONFIDENCE_EMOJI = {
    "HIGH": "🔥",
    "MEDIUM": "⚡",
    "LOW": "💡",
}

SIGNAL_EMOJI = {
    "SMART_WALLET_BUY": "🧠",
    "COORDINATED_BUY": "🤝",
    "EARLY_ENTRY": "🚀",
    "PRE_PUMP_PATTERN": "📈",
    "NEW_LIQUIDITY": "💧",
    "CLUSTER_BUY": "👥",
    "SMART_EXIT": "⚠️",
    "WHALE_MOVE": "🐋",
}


def _short_addr(addr: str) -> str:
    if not addr or len(addr) < 10:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


def format_signal(signal: Signal) -> str:
    emoji = SIGNAL_EMOJI.get(signal.signal_type, "📊")
    conf_emoji = CONFIDENCE_EMOJI.get(signal.confidence, "⚡")
    wallets = signal.get_wallets()

    lines: List[str] = [
        f"{emoji} <b>SMART MONEY SIGNAL</b>",
        f"<b>Type:</b> {signal.signal_type}  |  <b>Score:</b> {signal.score:.0f}/100",
    ]

    if signal.token_symbol:
        token_display = f"${signal.token_symbol}"
        if signal.token_address and signal.chain:
            explorer = get_explorer_address_url(signal.chain, signal.token_address)
            token_display = f'<a href="{explorer}">${signal.token_symbol}</a>'
        lines.append(f"<b>Token:</b> {token_display}")

    if signal.chain:
        lines.append(f"<b>Chain:</b> {signal.chain.upper()}")

    if wallets:
        wallet_strs = [_short_addr(w) for w in wallets[:5]]
        suffix = f" (+{len(wallets)-5} more)" if len(wallets) > 5 else ""
        lines.append(f"<b>Wallets:</b> {', '.join(wallet_strs)}{suffix}")

    lines.append(f"<b>What:</b> {signal.summary}")
    lines.append(f"<b>Why:</b> {signal.evidence}")
    lines.append(f"{conf_emoji} <b>Confidence:</b> {signal.confidence}")
    lines.append(
        f"<i>{signal.triggered_at.strftime('%Y-%m-%d %H:%M UTC')}</i>"
    )

    return "\n".join(lines)


async def send_signal(signal: Signal) -> bool:
    """Format and deliver a Signal to Telegram. Mark sent on success."""
    tg = get_telegram_client()
    text = format_signal(signal)
    ok = await tg.send_message(text)
    if ok:
        async with get_db() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(Signal).where(Signal.id == signal.id)
            )
            sig = result.scalar_one_or_none()
            if sig:
                sig.sent_telegram = True
    return ok


async def deliver_pending_signals() -> int:
    """Fetch all unsent signals and deliver them. Returns count sent."""
    from sqlalchemy import select

    async with get_db() as db:
        result = await db.execute(
            select(Signal).where(Signal.sent_telegram == False).order_by(Signal.triggered_at)
        )
        signals = result.scalars().all()

    count = 0
    for sig in signals:
        ok = await send_signal(sig)
        if ok:
            count += 1
        await asyncio.sleep(0.5)

    if count:
        logger.info("Delivered %d pending signals via Telegram", count)
    return count


async def send_startup_notification() -> None:
    """Send a startup ping to confirm bot is live."""
    tg = get_telegram_client()
    msg = (
        "🟢 <b>Smart Money Analytics Started</b>\n"
        f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>\n"
        "Scanning for smart money signals..."
    )
    await tg.send_message(msg)


async def send_status_report(stats: Dict[str, Any]) -> None:
    """Send periodic system status report."""
    tg = get_telegram_client()
    lines = [
        "📊 <b>Smart Money Status Report</b>",
        f"Top wallets tracked: {stats.get('top100_count', 0)}",
        f"Wallets scanned: {stats.get('wallets_scanned', 0)}",
        f"Signals today: {stats.get('signals_today', 0)}",
        f"API CU used (session): {stats.get('cu_used', 0):,}",
        f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>",
    ]
    await tg.send_message("\n".join(lines))
