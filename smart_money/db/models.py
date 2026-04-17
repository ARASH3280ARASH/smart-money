from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Wallet(Base):
    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_top100: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    last_synced: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_block: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list

    metrics: Mapped[Optional["WalletMetrics"]] = relationship(
        "WalletMetrics", back_populates="wallet", uselist=False
    )
    trades: Mapped[List["Trade"]] = relationship("Trade", back_populates="wallet")

    __table_args__ = (
        UniqueConstraint("address", "chain", name="uq_wallet_address_chain"),
        Index("ix_wallet_score", "score"),
        Index("ix_wallet_is_top100", "is_top100"),
    )

    def get_tags(self) -> List[str]:
        if not self.tags:
            return []
        return json.loads(self.tags)


class WalletMetrics(Base):
    __tablename__ = "wallet_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), unique=True)
    total_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    roi_pct: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)   # 0.0–1.0
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_holding_hours: Mapped[float] = mapped_column(Float, default=0.0)
    total_volume_usd: Mapped[float] = mapped_column(Float, default=0.0)
    avg_trade_size_usd: Mapped[float] = mapped_column(Float, default=0.0)
    early_entry_count: Mapped[int] = mapped_column(Integer, default=0)
    smart_exit_count: Mapped[int] = mapped_column(Integer, default=0)
    pre_pump_accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    last_trade_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    wallet: Mapped["Wallet"] = relationship("Wallet", back_populates="metrics")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), nullable=False)
    chain: Mapped[str] = mapped_column(String(20), nullable=False)
    token_address: Mapped[str] = mapped_column(String(64), nullable=False)
    token_symbol: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    tx_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    trade_type: Mapped[str] = mapped_column(String(8), nullable=False)  # buy | sell
    amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    token_amount: Mapped[float] = mapped_column(Float, default=0.0)
    price_usd: Mapped[float] = mapped_column(Float, default=0.0)
    block_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_early_entry: Mapped[bool] = mapped_column(Boolean, default=False)
    is_smart_exit: Mapped[bool] = mapped_column(Boolean, default=False)

    wallet: Mapped["Wallet"] = relationship("Wallet", back_populates="trades")

    __table_args__ = (
        UniqueConstraint("tx_hash", "wallet_id", name="uq_trade_tx_wallet"),
        Index("ix_trade_token", "token_address", "chain"),
        Index("ix_trade_timestamp", "timestamp"),
        Index("ix_trade_wallet_id", "wallet_id"),
    )


class Token(Base):
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    decimals: Mapped[int] = mapped_column(Integer, default=18)
    last_price_usd: Mapped[float] = mapped_column(Float, default=0.0)
    price_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    events: Mapped[List["TokenEvent"]] = relationship(
        "TokenEvent", back_populates="token"
    )

    __table_args__ = (
        UniqueConstraint("address", "chain", name="uq_token_address_chain"),
        Index("ix_token_symbol", "symbol"),
    )


class TokenEvent(Base):
    __tablename__ = "token_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tokens.id"), nullable=True
    )
    token_address: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    wallets_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    token: Mapped[Optional["Token"]] = relationship(
        "Token", back_populates="events"
    )

    def get_wallets(self) -> List[str]:
        if not self.wallets_json:
            return []
        return json.loads(self.wallets_json)

    def get_metadata(self) -> Dict[str, Any]:
        if not self.metadata_json:
            return {}
        return json.loads(self.metadata_json)

    __table_args__ = (
        Index("ix_token_event_type", "event_type"),
        Index("ix_token_event_timestamp", "timestamp"),
    )


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    token_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    token_symbol: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    chain: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    wallets_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    sent_telegram: Mapped[bool] = mapped_column(Boolean, default=False)

    def get_wallets(self) -> List[str]:
        if not self.wallets_json:
            return []
        return json.loads(self.wallets_json)

    __table_args__ = (
        Index("ix_signal_type", "signal_type"),
        Index("ix_signal_triggered_at", "triggered_at"),
        Index("ix_signal_sent", "sent_telegram"),
    )


class WalletRelationship(Base):
    __tablename__ = "wallet_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_a: Mapped[str] = mapped_column(String(64), nullable=False)
    wallet_b: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str] = mapped_column(String(20), nullable=False)
    relationship_score: Mapped[float] = mapped_column(Float, default=0.0)
    co_trade_count: Mapped[int] = mapped_column(Integer, default=0)
    shared_tokens_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cluster_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "wallet_a", "wallet_b", "chain", name="uq_wallet_rel"
        ),
        Index("ix_wallet_rel_score", "relationship_score"),
        Index("ix_wallet_rel_cluster", "cluster_id"),
    )


class ApiUsage(Base):
    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    cu_cost: Mapped[int] = mapped_column(Integer, default=1)
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    called_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    __table_args__ = (Index("ix_api_usage_called_at", "called_at"),)
