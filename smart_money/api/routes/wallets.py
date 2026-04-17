from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from db.models import Trade, Wallet, WalletMetrics
from db.session import get_db

router = APIRouter(prefix="/api/wallets", tags=["wallets"])


class LabelRequest(BaseModel):
    chain: str = "eth"
    label: str
    tags: List[str] = []


@router.get("")
async def list_wallets(
    limit: int = Query(100, le=500),
    chain: Optional[str] = None,
    min_score: float = Query(0.0),
):
    async with get_db() as db:
        query = (
            select(Wallet, WalletMetrics)
            .outerjoin(WalletMetrics, WalletMetrics.wallet_id == Wallet.id)
            .where(Wallet.is_active == True)
            .order_by(Wallet.score.desc())
        )
        if chain:
            query = query.where(Wallet.chain == chain)
        if min_score > 0:
            query = query.where(Wallet.score >= min_score)
        query = query.limit(limit)
        result = await db.execute(query)
        rows = result.all()

    return [
        {
            "id": w.id,
            "address": w.address,
            "chain": w.chain,
            "label": w.label or "",
            "tags": json.loads(w.tags) if w.tags else [],
            "score": round(w.score, 1),
            "rank": w.rank,
            "is_top100": w.is_top100,
            "last_synced": w.last_synced.isoformat() if w.last_synced else None,
            "first_seen": w.first_seen.isoformat() if w.first_seen else None,
            "metrics": {
                "realized_pnl_usd": round(m.realized_pnl_usd, 2) if m else 0,
                "win_rate": round(m.win_rate, 3) if m else 0,
                "trade_count": m.trade_count if m else 0,
                "roi_pct": round(m.roi_pct, 2) if m else 0,
                "avg_holding_hours": round(m.avg_holding_hours, 1) if m else 0,
                "total_volume_usd": round(m.total_volume_usd, 2) if m else 0,
                "early_entry_count": m.early_entry_count if m else 0,
                "smart_exit_count": m.smart_exit_count if m else 0,
                "last_trade_at": m.last_trade_at.isoformat() if m and m.last_trade_at else None,
            } if m else None,
        }
        for w, m in rows
    ]


@router.get("/{address}")
async def get_wallet(address: str, chain: str = Query("eth")):
    async with get_db() as db:
        result = await db.execute(
            select(Wallet, WalletMetrics)
            .outerjoin(WalletMetrics, WalletMetrics.wallet_id == Wallet.id)
            .where(Wallet.address == address.lower(), Wallet.chain == chain)
        )
        row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="Wallet not found")

    w, m = row

    # Recent trades
    async with get_db() as db:
        result = await db.execute(
            select(Trade)
            .where(Trade.wallet_id == w.id)
            .order_by(Trade.timestamp.desc())
            .limit(50)
        )
        trades = result.scalars().all()

    return {
        "address": w.address,
        "chain": w.chain,
        "label": w.label or "",
        "tags": json.loads(w.tags) if w.tags else [],
        "score": round(w.score, 1),
        "rank": w.rank,
        "is_top100": w.is_top100,
        "metrics": {
            "realized_pnl_usd": round(m.realized_pnl_usd, 2) if m else 0,
            "win_rate": round(m.win_rate, 3) if m else 0,
            "trade_count": m.trade_count if m else 0,
            "roi_pct": round(m.roi_pct, 2) if m else 0,
            "avg_holding_hours": round(m.avg_holding_hours, 1) if m else 0,
            "total_volume_usd": round(m.total_volume_usd, 2) if m else 0,
            "early_entry_count": m.early_entry_count if m else 0,
            "smart_exit_count": m.smart_exit_count if m else 0,
        } if m else None,
        "recent_trades": [
            {
                "tx_hash": t.tx_hash[:12] + "...",
                "token_symbol": t.token_symbol,
                "trade_type": t.trade_type,
                "amount_usd": round(t.amount_usd, 2),
                "price_usd": t.price_usd,
                "timestamp": t.timestamp.isoformat(),
                "is_early_entry": t.is_early_entry,
                "is_smart_exit": t.is_smart_exit,
            }
            for t in trades
        ],
    }


@router.post("/{address}/label")
async def set_wallet_label(address: str, body: LabelRequest):
    async with get_db() as db:
        result = await db.execute(
            select(Wallet).where(
                Wallet.address == address.lower(),
                Wallet.chain == body.chain,
            )
        )
        wallet = result.scalar_one_or_none()
        if not wallet:
            # Create if not exists
            wallet = Wallet(
                address=address.lower(),
                chain=body.chain,
                label=body.label,
                tags=json.dumps(body.tags),
            )
            db.add(wallet)
        else:
            wallet.label = body.label
            wallet.tags = json.dumps(body.tags)

    return {"status": "ok", "address": address, "label": body.label}


@router.delete("/{address}/label")
async def remove_wallet_label(address: str, chain: str = Query("eth")):
    async with get_db() as db:
        result = await db.execute(
            select(Wallet).where(
                Wallet.address == address.lower(), Wallet.chain == chain
            )
        )
        wallet = result.scalar_one_or_none()
        if wallet:
            wallet.label = None
            wallet.tags = None
    return {"status": "ok"}
