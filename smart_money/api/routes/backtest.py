from __future__ import annotations

from fastapi import APIRouter, Query

from analytics.backtester import run_backtest

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# In-memory cache of last backtest result
_last_report = None


@router.get("")
async def get_backtest(
    hours_back: int = Query(168, ge=24, le=720),
    win_threshold: float = Query(5.0, ge=1.0),
    refresh: bool = Query(False),
):
    """
    Run or return cached backtest results.
    Pass ?refresh=true to force a fresh run (uses API CU).
    """
    global _last_report

    if _last_report is None or refresh:
        report = await run_backtest(
            hours_back=hours_back,
            win_threshold_pct=win_threshold,
        )
        _last_report = report.to_dict()

    return _last_report
