from __future__ import annotations

"""
Fetches wallet data from Moralis and persists it to the database.
Handles wallet history, PnL, balances, and trade record creation.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from clients.moralis import get_moralis_client
from config.settings import get_settings
from db.models import Token, Trade, Wallet, WalletMetrics
from db.session import get_db
from utils.logger import get_logger

logger = get_logger(__name__)


def _parse_timestamp(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts
    try:
        # ISO format with or without Z suffix
        s = str(ts).replace("Z", "+00:00")
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except Exception:
        return None


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


async def upsert_wallet(address: str, chain: str, label: Optional[str] = None) -> Wallet:
    """Get or create a wallet record."""
    async with get_db() as db:
        result = await db.execute(
            select(Wallet).where(Wallet.address == address, Wallet.chain == chain)
        )
        wallet = result.scalar_one_or_none()
        if wallet is None:
            wallet = Wallet(address=address, chain=chain, label=label)
            db.add(wallet)
            await db.flush()
            logger.debug("New wallet %s on %s", address[:10], chain)
        elif label and not wallet.label:
            wallet.label = label
        return wallet


async def fetch_and_store_wallet_history(
    wallet: Wallet, days_back: int = 90
) -> int:
    """Fetch transaction history, parse swaps, persist trades. Returns new trade count."""
    client = get_moralis_client()
    from_date = (datetime.utcnow() - timedelta(days=days_back)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    cursor = None
    new_trades = 0
    page = 0
    max_pages = 10  # Safety cap per fetch cycle

    while page < max_pages:
        data = await client.get_wallet_history(
            wallet.address, wallet.chain, limit=100, from_date=from_date, cursor=cursor
        )
        if not data:
            break

        transactions = data.get("result", [])
        if not transactions:
            break

        trades_batch = []
        for tx in transactions:
            parsed = _parse_transaction(tx, wallet)
            if parsed:
                trades_batch.extend(parsed)

        new_trades += await _persist_trades(wallet, trades_batch)

        cursor = data.get("cursor")
        if not cursor:
            break
        page += 1

    # Update last_synced and last_block
    async with get_db() as db:
        result = await db.execute(
            select(Wallet).where(Wallet.id == wallet.id)
        )
        w = result.scalar_one_or_none()
        if w:
            w.last_synced = datetime.utcnow()

    return new_trades


def _parse_transaction(tx: Dict, wallet: Wallet) -> List[Dict]:
    """Parse a Moralis transaction entry into zero or more trade dicts."""
    trades = []
    category = tx.get("category", "")
    tx_hash = tx.get("hash", "")
    block = tx.get("block_number")
    ts = _parse_timestamp(tx.get("block_timestamp"))

    if not ts or not tx_hash:
        return trades

    # Moralis decoded swap format
    erc20_transfers = tx.get("erc20_transfers", [])
    native_transfers = tx.get("native_transfers", [])

    # Look for swap patterns: wallet sends token A, receives token B
    sent_tokens = [
        t for t in erc20_transfers
        if t.get("from_address", "").lower() == wallet.address.lower()
    ]
    received_tokens = [
        t for t in erc20_transfers
        if t.get("to_address", "").lower() == wallet.address.lower()
    ]

    # Determine if buy or sell by checking native currency direction
    native_out = any(
        t.get("from_address", "").lower() == wallet.address.lower()
        for t in native_transfers
    )
    native_in = any(
        t.get("to_address", "").lower() == wallet.address.lower()
        for t in native_transfers
    )

    for transfer in received_tokens:
        # Wallet received token → BUY
        amount_usd = _safe_float(transfer.get("value_formatted")) * _safe_float(
            transfer.get("usd_price", 0)
        )
        if amount_usd == 0:
            amount_usd = _safe_float(transfer.get("amount_usd"))

        trades.append({
            "tx_hash": tx_hash,
            "token_address": transfer.get("address", "").lower(),
            "token_symbol": transfer.get("token_symbol", ""),
            "trade_type": "buy",
            "amount_usd": amount_usd,
            "token_amount": _safe_float(transfer.get("value_formatted")),
            "price_usd": _safe_float(transfer.get("usd_price")),
            "block_number": int(block) if block else None,
            "timestamp": ts,
        })

    for transfer in sent_tokens:
        # Wallet sent token → SELL
        amount_usd = _safe_float(transfer.get("value_formatted")) * _safe_float(
            transfer.get("usd_price", 0)
        )
        if amount_usd == 0:
            amount_usd = _safe_float(transfer.get("amount_usd"))

        trades.append({
            "tx_hash": tx_hash,
            "token_address": transfer.get("address", "").lower(),
            "token_symbol": transfer.get("token_symbol", ""),
            "trade_type": "sell",
            "amount_usd": amount_usd,
            "token_amount": _safe_float(transfer.get("value_formatted")),
            "price_usd": _safe_float(transfer.get("usd_price")),
            "block_number": int(block) if block else None,
            "timestamp": ts,
        })

    return trades


async def _persist_trades(wallet: Wallet, trade_dicts: List[Dict]) -> int:
    """Insert new trades, skip duplicates. Returns count of new inserts."""
    if not trade_dicts:
        return 0

    count = 0
    async with get_db() as db:
        for td in trade_dicts:
            # Check duplicate
            existing = await db.execute(
                select(Trade).where(
                    Trade.tx_hash == td["tx_hash"],
                    Trade.wallet_id == wallet.id,
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue

            trade = Trade(
                wallet_id=wallet.id,
                chain=wallet.chain,
                token_address=td["token_address"],
                token_symbol=td["token_symbol"],
                tx_hash=td["tx_hash"],
                trade_type=td["trade_type"],
                amount_usd=td["amount_usd"],
                token_amount=td["token_amount"],
                price_usd=td["price_usd"],
                block_number=td["block_number"],
                timestamp=td["timestamp"],
            )
            db.add(trade)
            count += 1

            # Upsert token record
            await _upsert_token(
                db,
                td["token_address"],
                wallet.chain,
                td["token_symbol"],
            )

    return count


async def _upsert_token(db, address: str, chain: str, symbol: str) -> None:
    if not address:
        return
    result = await db.execute(
        select(Token).where(Token.address == address, Token.chain == chain)
    )
    token = result.scalar_one_or_none()
    if token is None:
        token = Token(
            address=address,
            chain=chain,
            symbol=symbol or "",
            name="",
        )
        db.add(token)
    elif symbol and not token.symbol:
        token.symbol = symbol


async def fetch_and_store_pnl(wallet: Wallet) -> Optional[Dict]:
    """Fetch PnL summary from Moralis and store in WalletMetrics."""
    client = get_moralis_client()
    summary = await client.get_wallet_pnl_summary(wallet.address, wallet.chain)
    if not summary:
        return None

    async with get_db() as db:
        result = await db.execute(
            select(WalletMetrics).where(WalletMetrics.wallet_id == wallet.id)
        )
        metrics = result.scalar_one_or_none()
        if metrics is None:
            metrics = WalletMetrics(wallet_id=wallet.id)
            db.add(metrics)

        metrics.realized_pnl_usd = _safe_float(summary.get("total_realized_profit_usd"))
        metrics.total_volume_usd = _safe_float(summary.get("total_volume_traded_usd"))
        metrics.trade_count = int(summary.get("total_count_of_trades", 0) or 0)
        metrics.win_count = int(summary.get("total_wins", 0) or 0)
        win_rate_raw = summary.get("winrate")
        if win_rate_raw is not None:
            metrics.win_rate = _safe_float(win_rate_raw) / 100.0
        elif metrics.trade_count > 0:
            metrics.win_rate = metrics.win_count / metrics.trade_count

        metrics.updated_at = datetime.utcnow()

    return summary


async def enrich_wallet_from_moralis(wallet: Wallet) -> None:
    """Full enrichment pass: history + PnL + net worth."""
    await fetch_and_store_wallet_history(wallet)
    await fetch_and_store_pnl(wallet)


async def bulk_upsert_wallets(
    addresses: List[Tuple[str, str]]
) -> List[Wallet]:
    """addresses: list of (address, chain). Returns Wallet objects."""
    wallets = []
    for address, chain in addresses:
        w = await upsert_wallet(address, chain)
        wallets.append(w)
    return wallets
