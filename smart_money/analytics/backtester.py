from __future__ import annotations

"""
Signal backtest validator.
For each past signal, fetches the token price at signal time and N hours later,
then computes whether the signal was a winning prediction.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from clients.moralis import get_moralis_client
from db.models import Signal
from db.session import get_db
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SignalResult:
    signal_id: int
    signal_type: str
    token_address: str
    token_symbol: str
    chain: str
    score: float
    triggered_at: datetime
    price_at_signal: float
    price_4h: float
    price_24h: float
    price_72h: float
    pct_4h: float
    pct_24h: float
    pct_72h: float
    is_win_4h: bool
    is_win_24h: bool
    is_win_72h: bool


@dataclass
class BacktestReport:
    generated_at: datetime
    signals_evaluated: int
    by_type: List[Dict[str, Any]] = field(default_factory=list)
    overall_win_rate_24h: float = 0.0
    best_signal_type: str = ""
    worst_signal_type: str = ""
    results: List[SignalResult] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "signals_evaluated": self.signals_evaluated,
            "overall_win_rate_24h": round(self.overall_win_rate_24h, 3),
            "best_signal_type": self.best_signal_type,
            "worst_signal_type": self.worst_signal_type,
            "by_type": self.by_type,
            "results": [
                {
                    "signal_id": r.signal_id,
                    "type": r.signal_type,
                    "token": r.token_symbol,
                    "chain": r.chain,
                    "score": round(r.score, 1),
                    "triggered_at": r.triggered_at.isoformat(),
                    "price_at_signal": r.price_at_signal,
                    "pct_4h": round(r.pct_4h, 2),
                    "pct_24h": round(r.pct_24h, 2),
                    "pct_72h": round(r.pct_72h, 2),
                    "win_4h": r.is_win_4h,
                    "win_24h": r.is_win_24h,
                    "win_72h": r.is_win_72h,
                }
                for r in self.results
            ],
        }


async def _get_price_at(
    token_address: str,
    chain: str,
    at_time: datetime,
) -> float:
    """Fetch token price at a specific historical time via Moralis."""
    client = get_moralis_client()
    # Use toDate param for historical price lookup
    to_date = at_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        data = await client._request(
            "GET",
            f"https://deep-index.moralis.io/api/v2.2/erc20/{token_address}/price",
            params={"chain": chain, "toDate": to_date},
            endpoint_key="token_price",
        )
        if data and "usdPrice" in data:
            return float(data["usdPrice"])
    except Exception as e:
        logger.debug("Price fetch failed for %s at %s: %s", token_address[:10], to_date, e)
    return 0.0


def _pct_change(price_before: float, price_after: float) -> float:
    if price_before <= 0 or price_after <= 0:
        return 0.0
    return (price_after - price_before) / price_before * 100


async def run_backtest(
    hours_back: int = 168,  # 7 days
    win_threshold_pct: float = 5.0,
    max_signals: int = 100,
) -> BacktestReport:
    """
    Run backtest on recent signals.
    Fetches historical prices at signal time and at +4h, +24h, +72h.
    """
    since = datetime.utcnow() - timedelta(hours=hours_back)
    cutoff_72h = datetime.utcnow() - timedelta(hours=72)

    async with get_db() as db:
        result = await db.execute(
            select(Signal)
            .where(
                Signal.triggered_at >= since,
                Signal.triggered_at <= cutoff_72h,  # need 72h of data
                Signal.token_address.isnot(None),
                Signal.sent_telegram == True,
            )
            .order_by(Signal.triggered_at.desc())
            .limit(max_signals)
        )
        signals: List[Signal] = result.scalars().all()

    logger.info("Backtesting %d signals from last %dh", len(signals), hours_back)

    results: List[SignalResult] = []

    for sig in signals:
        if not sig.token_address:
            continue

        price_now = await _get_price_at(
            sig.token_address, sig.chain, sig.triggered_at
        )
        if price_now <= 0:
            continue

        p4h = await _get_price_at(
            sig.token_address, sig.chain,
            sig.triggered_at + timedelta(hours=4)
        )
        p24h = await _get_price_at(
            sig.token_address, sig.chain,
            sig.triggered_at + timedelta(hours=24)
        )
        p72h = await _get_price_at(
            sig.token_address, sig.chain,
            sig.triggered_at + timedelta(hours=72)
        )

        pct4h = _pct_change(price_now, p4h)
        pct24h = _pct_change(price_now, p24h)
        pct72h = _pct_change(price_now, p72h)

        results.append(SignalResult(
            signal_id=sig.id,
            signal_type=sig.signal_type,
            token_address=sig.token_address,
            token_symbol=sig.token_symbol or "",
            chain=sig.chain,
            score=sig.score,
            triggered_at=sig.triggered_at,
            price_at_signal=price_now,
            price_4h=p4h,
            price_24h=p24h,
            price_72h=p72h,
            pct_4h=pct4h,
            pct_24h=pct24h,
            pct_72h=pct72h,
            is_win_4h=pct4h >= win_threshold_pct,
            is_win_24h=pct24h >= win_threshold_pct,
            is_win_72h=pct72h >= win_threshold_pct,
        ))

    # Aggregate by signal type
    by_type: Dict[str, Dict] = {}
    for r in results:
        t = r.signal_type
        if t not in by_type:
            by_type[t] = {
                "signal_type": t,
                "total": 0,
                "wins_4h": 0, "wins_24h": 0, "wins_72h": 0,
                "sum_pct_4h": 0.0, "sum_pct_24h": 0.0, "sum_pct_72h": 0.0,
            }
        by_type[t]["total"] += 1
        by_type[t]["wins_4h"] += int(r.is_win_4h)
        by_type[t]["wins_24h"] += int(r.is_win_24h)
        by_type[t]["wins_72h"] += int(r.is_win_72h)
        by_type[t]["sum_pct_4h"] += r.pct_4h
        by_type[t]["sum_pct_24h"] += r.pct_24h
        by_type[t]["sum_pct_72h"] += r.pct_72h

    by_type_list = []
    for t, d in by_type.items():
        n = d["total"]
        entry = {
            "signal_type": t,
            "total": n,
            "win_rate_4h": round(d["wins_4h"] / n, 3) if n else 0,
            "win_rate_24h": round(d["wins_24h"] / n, 3) if n else 0,
            "win_rate_72h": round(d["wins_72h"] / n, 3) if n else 0,
            "avg_gain_pct_4h": round(d["sum_pct_4h"] / n, 2) if n else 0,
            "avg_gain_pct_24h": round(d["sum_pct_24h"] / n, 2) if n else 0,
            "avg_gain_pct_72h": round(d["sum_pct_72h"] / n, 2) if n else 0,
        }
        by_type_list.append(entry)

    by_type_list.sort(key=lambda x: x["win_rate_24h"], reverse=True)

    total_wins_24h = sum(r.is_win_24h for r in results)
    overall_wr = total_wins_24h / len(results) if results else 0.0

    best = by_type_list[0]["signal_type"] if by_type_list else ""
    worst = by_type_list[-1]["signal_type"] if by_type_list else ""

    report = BacktestReport(
        generated_at=datetime.utcnow(),
        signals_evaluated=len(results),
        by_type=by_type_list,
        overall_win_rate_24h=overall_wr,
        best_signal_type=best,
        worst_signal_type=worst,
        results=results,
    )

    logger.info(
        "Backtest complete: %d signals, overall 24h win rate %.1f%%",
        len(results), overall_wr * 100,
    )
    return report
