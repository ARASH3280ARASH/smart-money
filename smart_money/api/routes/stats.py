from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from db.models import ApiUsage, Signal, Wallet, WalletMetrics
from db.session import get_db
from utils.rate_limiter import get_rate_limiter

router = APIRouter(prefix="/api/stats", tags=["stats"])

_start_time = datetime.utcnow()


@router.get("")
async def get_stats():
    since_24h = datetime.utcnow() - timedelta(hours=24)
    since_1h = datetime.utcnow() - timedelta(hours=1)

    async with get_db() as db:
        # Wallet counts
        total_wallets = (await db.execute(
            select(func.count(Wallet.id)).where(Wallet.is_active == True)
        )).scalar() or 0

        top100_count = (await db.execute(
            select(func.count(Wallet.id)).where(
                Wallet.is_top100 == True, Wallet.is_active == True
            )
        )).scalar() or 0

        # Signal counts
        signals_24h = (await db.execute(
            select(func.count(Signal.id)).where(Signal.triggered_at >= since_24h)
        )).scalar() or 0

        signals_1h = (await db.execute(
            select(func.count(Signal.id)).where(Signal.triggered_at >= since_1h)
        )).scalar() or 0

        # Signal breakdown by type
        type_counts = (await db.execute(
            select(Signal.signal_type, func.count(Signal.id).label("cnt"))
            .where(Signal.triggered_at >= since_24h)
            .group_by(Signal.signal_type)
            .order_by(func.count(Signal.id).desc())
        )).all()

        # API usage in last hour
        cu_1h = (await db.execute(
            select(func.sum(ApiUsage.cu_cost)).where(ApiUsage.called_at >= since_1h)
        )).scalar() or 0

        cu_24h = (await db.execute(
            select(func.sum(ApiUsage.cu_cost)).where(ApiUsage.called_at >= since_24h)
        )).scalar() or 0

        api_calls_24h = (await db.execute(
            select(func.count(ApiUsage.id)).where(ApiUsage.called_at >= since_24h)
        )).scalar() or 0

        # Top wallet score
        top_score_row = (await db.execute(
            select(Wallet.address, Wallet.score, Wallet.label)
            .where(Wallet.is_top100 == True)
            .order_by(Wallet.score.desc())
            .limit(1)
        )).first()

        # Average wallet score
        avg_score = (await db.execute(
            select(func.avg(Wallet.score)).where(Wallet.is_top100 == True)
        )).scalar() or 0

    limiter = get_rate_limiter()
    cu_pressure = await limiter.current_pressure()
    uptime_sec = int((datetime.utcnow() - _start_time).total_seconds())

    return {
        "uptime_seconds": uptime_sec,
        "uptime_human": _format_uptime(uptime_sec),
        "wallets": {
            "total": total_wallets,
            "top100": top100_count,
            "avg_score": round(float(avg_score), 1),
            "top_wallet": {
                "address": top_score_row[0] if top_score_row else None,
                "score": round(top_score_row[1], 1) if top_score_row else 0,
                "label": top_score_row[2] if top_score_row else None,
            } if top_score_row else None,
        },
        "signals": {
            "last_24h": signals_24h,
            "last_1h": signals_1h,
            "by_type_24h": {r.signal_type: r.cnt for r in type_counts},
        },
        "api": {
            "cu_used_1h": int(cu_1h),
            "cu_used_24h": int(cu_24h),
            "calls_24h": api_calls_24h,
            "cu_pressure_pct": round(cu_pressure * 100, 1),
            "cu_budget_per_session": get_rate_limiter().total_cu_consumed,
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/api-usage")
async def get_api_usage(hours: int = 24):
    since = datetime.utcnow() - timedelta(hours=hours)
    async with get_db() as db:
        result = await db.execute(
            select(
                ApiUsage.endpoint,
                func.count(ApiUsage.id).label("calls"),
                func.sum(ApiUsage.cu_cost).label("total_cu"),
            )
            .where(ApiUsage.called_at >= since)
            .group_by(ApiUsage.endpoint)
            .order_by(func.sum(ApiUsage.cu_cost).desc())
            .limit(20)
        )
        rows = result.all()

    return [
        {"endpoint": r.endpoint, "calls": r.calls, "total_cu": int(r.total_cu or 0)}
        for r in rows
    ]


def _format_uptime(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m {s}s"
