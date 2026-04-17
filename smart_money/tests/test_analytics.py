from __future__ import annotations

"""Unit tests for wallet analytics computations."""

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.wallet_analytics import _compute_pnl, _compute_avg_holding_time


@dataclass
class FakeTrade:
    id: int = 1
    wallet_id: int = 1
    chain: str = "eth"
    token_address: str = "0xabc"
    token_symbol: str = "TEST"
    tx_hash: str = "0x123"
    trade_type: str = "buy"
    amount_usd: float = 10_000.0
    token_amount: float = 100.0
    price_usd: float = 100.0
    block_number: Optional[int] = 12345
    timestamp: datetime = field(default_factory=datetime.utcnow)
    is_early_entry: bool = False
    is_smart_exit: bool = False


class TestPnlComputation:
    def test_profitable_trade(self):
        now = datetime.utcnow()
        buy = FakeTrade(trade_type="buy", price_usd=100, token_amount=10,
                        timestamp=now - timedelta(hours=2))
        sell = FakeTrade(trade_type="sell", price_usd=200, token_amount=10,
                         timestamp=now)
        result = _compute_pnl([buy, sell])
        assert result["realized_pnl"] == pytest.approx(1000.0, abs=1)
        assert result["win_count"] == 1
        assert result["completed_trades"] == 1

    def test_losing_trade(self):
        now = datetime.utcnow()
        buy = FakeTrade(trade_type="buy", price_usd=200, token_amount=10,
                        timestamp=now - timedelta(hours=2))
        sell = FakeTrade(trade_type="sell", price_usd=100, token_amount=10,
                         timestamp=now)
        result = _compute_pnl([buy, sell])
        assert result["realized_pnl"] == pytest.approx(-1000.0, abs=1)
        assert result["win_count"] == 0
        assert result["completed_trades"] == 1

    def test_no_sells(self):
        buy = FakeTrade(trade_type="buy")
        result = _compute_pnl([buy])
        assert result["realized_pnl"] == 0.0
        assert result["completed_trades"] == 0

    def test_win_rate_calculation(self):
        now = datetime.utcnow()
        trades = [
            FakeTrade(trade_type="buy", price_usd=100, token_amount=1,
                      token_address="0xa", timestamp=now - timedelta(hours=3)),
            FakeTrade(trade_type="sell", price_usd=200, token_amount=1,
                      token_address="0xa", timestamp=now - timedelta(hours=2)),
            FakeTrade(trade_type="buy", price_usd=100, token_amount=1,
                      token_address="0xb", timestamp=now - timedelta(hours=1)),
            FakeTrade(trade_type="sell", price_usd=50, token_amount=1,
                      token_address="0xb", timestamp=now),
        ]
        result = _compute_pnl(trades)
        assert result["completed_trades"] == 2
        assert result["win_count"] == 1


class TestHoldingTime:
    def test_basic_holding_time(self):
        now = datetime.utcnow()
        buy = FakeTrade(trade_type="buy", timestamp=now - timedelta(hours=24))
        sell = FakeTrade(trade_type="sell", timestamp=now)
        avg = _compute_avg_holding_time([buy, sell])
        assert avg == pytest.approx(24.0, abs=0.1)

    def test_no_sells_returns_zero(self):
        buy = FakeTrade(trade_type="buy")
        avg = _compute_avg_holding_time([buy])
        assert avg == 0.0

    def test_multiple_pairs(self):
        now = datetime.utcnow()
        trades = [
            FakeTrade(trade_type="buy", token_address="0xa",
                      timestamp=now - timedelta(hours=10)),
            FakeTrade(trade_type="sell", token_address="0xa",
                      timestamp=now - timedelta(hours=2)),
            FakeTrade(trade_type="buy", token_address="0xb",
                      timestamp=now - timedelta(hours=6)),
            FakeTrade(trade_type="sell", token_address="0xb",
                      timestamp=now - timedelta(hours=2)),
        ]
        avg = _compute_avg_holding_time(trades)
        assert avg == pytest.approx(6.0, abs=0.5)
