from __future__ import annotations

"""
Signal formatting utilities for reports, logs, and export.
"""

import json
from datetime import datetime
from typing import Any, Dict, List

from db.models import Signal
from utils.logger import get_logger

logger = get_logger(__name__)


def signal_to_dict(signal: Signal) -> Dict[str, Any]:
    return {
        "id": signal.id,
        "type": signal.signal_type,
        "token": signal.token_symbol,
        "token_address": signal.token_address,
        "chain": signal.chain,
        "wallets": signal.get_wallets(),
        "score": signal.score,
        "confidence": signal.confidence,
        "summary": signal.summary,
        "evidence": signal.evidence,
        "triggered_at": signal.triggered_at.isoformat(),
        "sent_telegram": signal.sent_telegram,
    }


def format_signal_text(signal: Signal) -> str:
    """Plain-text format for logging/export."""
    wallets = signal.get_wallets()
    wallet_str = ", ".join(w[:10] + "..." for w in wallets[:3])
    if len(wallets) > 3:
        wallet_str += f" (+{len(wallets)-3})"

    lines = [
        f"[{signal.signal_type}] Score: {signal.score:.0f}/100 | Confidence: {signal.confidence}",
        f"Token: {signal.token_symbol or 'N/A'} ({signal.chain.upper()})",
        f"Wallets: {wallet_str or 'N/A'}",
        f"Summary: {signal.summary}",
        f"Evidence: {signal.evidence}",
        f"Time: {signal.triggered_at.strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    return "\n".join(lines)


def signals_to_json_report(signals: List[Signal]) -> str:
    """Export a list of signals as JSON."""
    return json.dumps([signal_to_dict(s) for s in signals], indent=2, default=str)


def get_signal_priority(signal: Signal) -> int:
    """Return delivery priority (lower = higher priority)."""
    priority_map = {
        "PRE_PUMP_PATTERN": 1,
        "COORDINATED_BUY": 2,
        "CLUSTER_BUY": 3,
        "EARLY_ENTRY": 4,
        "SMART_WALLET_BUY": 5,
        "WHALE_MOVE": 6,
        "SMART_EXIT": 7,
        "NEW_LIQUIDITY": 8,
    }
    base = priority_map.get(signal.signal_type, 9)
    # Boost by score: higher score = higher priority within type
    score_boost = max(0, 1 - int(signal.score / 20))
    return base * 10 + score_boost


def sort_signals_by_priority(signals: List[Signal]) -> List[Signal]:
    return sorted(signals, key=get_signal_priority)
