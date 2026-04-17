from __future__ import annotations

"""Unit tests for the scoring engine."""

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.scoring import (
    score_win_rate,
    score_pnl_quality,
    score_roi_consistency,
    score_early_entry,
    score_smart_exit,
    score_capital_size,
    score_recency,
    compute_score,
    WEIGHTS,
)


@dataclass
class FakeMetrics:
    wallet_id: int = 1
    total_pnl_usd: float = 0.0
    realized_pnl_usd: float = 10_000.0
    unrealized_pnl_usd: float = 0.0
    roi_pct: float = 25.0
    win_rate: float = 0.65
    trade_count: int = 50
    win_count: int = 33
    avg_holding_hours: float = 48.0
    total_volume_usd: float = 200_000.0
    avg_trade_size_usd: float = 4_000.0
    early_entry_count: int = 10
    smart_exit_count: int = 5
    pre_pump_accuracy: float = 0.65
    last_trade_at: Optional[datetime] = field(
        default_factory=lambda: datetime.utcnow() - timedelta(days=5)
    )
    updated_at: Optional[datetime] = field(default_factory=datetime.utcnow)


class TestWinRate:
    def test_zero_win_rate(self):
        assert score_win_rate(0.0, 50) == 0.0

    def test_full_win_rate(self):
        assert score_win_rate(0.65, 50) == pytest.approx(1.0, abs=0.01)

    def test_low_trade_count_penalty(self):
        assert score_win_rate(0.90, 3) < score_win_rate(0.90, 50)

    def test_above_threshold(self):
        assert score_win_rate(0.80, 50) == 1.0


class TestPnlQuality:
    def test_zero_pnl(self):
        assert score_pnl_quality(0) == 0.0

    def test_negative_pnl(self):
        assert score_pnl_quality(-5000) == 0.0

    def test_high_pnl(self):
        assert score_pnl_quality(500_000) == pytest.approx(1.0, abs=0.01)

    def test_mid_pnl(self):
        v = score_pnl_quality(50_000)
        assert 0.0 < v < 1.0


class TestRoiConsistency:
    def test_zero(self):
        assert score_roi_consistency(0) == 0.0

    def test_negative(self):
        assert score_roi_consistency(-10) == 0.0

    def test_target(self):
        assert score_roi_consistency(20.0) == pytest.approx(1.0, abs=0.01)

    def test_partial(self):
        assert score_roi_consistency(10.0) == pytest.approx(0.5, abs=0.01)


class TestEarlyEntry:
    def test_zero_entries(self):
        assert score_early_entry(0, 50) == 0.0

    def test_full_rate(self):
        assert score_early_entry(15, 50) == pytest.approx(1.0, abs=0.01)

    def test_no_trades(self):
        assert score_early_entry(5, 0) == 0.0


class TestRecency:
    def test_recent(self):
        recent = datetime.utcnow() - timedelta(days=2)
        assert score_recency(recent) == 1.0

    def test_30_days(self):
        d30 = datetime.utcnow() - timedelta(days=25)
        assert score_recency(d30) == pytest.approx(0.7, abs=0.1)

    def test_old(self):
        old = datetime.utcnow() - timedelta(days=200)
        assert score_recency(old) == 0.0

    def test_none(self):
        assert score_recency(None) == 0.0


class TestCompositeScore:
    def test_weights_sum_to_one(self):
        total = sum(WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=0.001)

    def test_good_wallet_scores_high(self):
        m = FakeMetrics(
            realized_pnl_usd=100_000,
            win_rate=0.75,
            roi_pct=35.0,
            early_entry_count=15,
            smart_exit_count=10,
            avg_trade_size_usd=20_000,
            trade_count=50,
            last_trade_at=datetime.utcnow() - timedelta(days=2),
        )
        score, _ = compute_score(m, high_score_co_traders=3)
        assert score >= 60

    def test_bad_wallet_scores_low(self):
        m = FakeMetrics(
            realized_pnl_usd=100,
            win_rate=0.20,
            roi_pct=-5.0,
            early_entry_count=0,
            smart_exit_count=0,
            avg_trade_size_usd=100,
            trade_count=5,
            last_trade_at=datetime.utcnow() - timedelta(days=300),
        )
        score, _ = compute_score(m)
        assert score < 25

    def test_score_bounds(self):
        m = FakeMetrics()
        score, factors = compute_score(m)
        assert 0 <= score <= 100
        for k, v in factors.items():
            assert 0 <= v <= 1, f"Factor {k} out of bounds: {v}"
