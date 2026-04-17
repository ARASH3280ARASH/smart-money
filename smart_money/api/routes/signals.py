from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select

from db.models import Signal
from db.session import get_db

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("")
async def list_signals(
    limit: int = Query(50, le=200),
    signal_type: Optional[str] = None,
    chain: Optional[str] = None,
    min_score: float = Query(0.0),
    sent_only: bool = Query(False),
):
    async with get_db() as db:
        query = select(Signal).order_by(Signal.triggered_at.desc())
        if signal_type:
            query = query.where(Signal.signal_type == signal_type.upper())
        if chain:
            query = query.where(Signal.chain == chain)
        if min_score > 0:
            query = query.where(Signal.score >= min_score)
        if sent_only:
            query = query.where(Signal.sent_telegram == True)
        query = query.limit(limit)
        result = await db.execute(query)
        signals = result.scalars().all()

    return [
        {
            "id": s.id,
            "type": s.signal_type,
            "token": s.token_symbol,
            "token_address": s.token_address,
            "chain": s.chain,
            "wallets": json.loads(s.wallets_json) if s.wallets_json else [],
            "score": round(s.score, 1),
            "confidence": s.confidence,
            "summary": s.summary,
            "evidence": s.evidence,
            "triggered_at": s.triggered_at.isoformat(),
            "sent_telegram": s.sent_telegram,
        }
        for s in signals
    ]


@router.get("/types")
async def list_signal_types():
    return [
        "SMART_WALLET_BUY",
        "COORDINATED_BUY",
        "EARLY_ENTRY",
        "PRE_PUMP_PATTERN",
        "NEW_LIQUIDITY",
        "CLUSTER_BUY",
        "SMART_EXIT",
        "WHALE_MOVE",
    ]


@router.get("/{signal_id}")
async def get_signal(signal_id: int):
    from fastapi import HTTPException

    async with get_db() as db:
        result = await db.execute(
            select(Signal).where(Signal.id == signal_id)
        )
        sig = result.scalar_one_or_none()

    if not sig:
        raise HTTPException(404, "Signal not found")

    return {
        "id": sig.id,
        "type": sig.signal_type,
        "token": sig.token_symbol,
        "token_address": sig.token_address,
        "chain": sig.chain,
        "wallets": json.loads(sig.wallets_json) if sig.wallets_json else [],
        "score": round(sig.score, 1),
        "confidence": sig.confidence,
        "summary": sig.summary,
        "evidence": sig.evidence,
        "triggered_at": sig.triggered_at.isoformat(),
        "sent_telegram": sig.sent_telegram,
    }
