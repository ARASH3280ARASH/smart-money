from __future__ import annotations

"""
Moralis Streams webhook endpoint.
Receives real-time blockchain events, verifies signature, processes trades.
"""

import asyncio
import json
from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request, Response

from clients.streams_client import parse_stream_event, verify_webhook_signature
from config.settings import get_settings
from db.models import Wallet
from db.session import get_db
from utils.logger import get_logger

router = APIRouter(tags=["streams"])
logger = get_logger(__name__)


@router.post("/streams/webhook")
async def receive_stream_event(request: Request):
    """
    Moralis Streams webhook endpoint.
    Verifies HMAC signature, parses events, persists trades, fires signals.
    """
    settings = get_settings()
    body = await request.body()

    # Verify Moralis signature
    signature = request.headers.get("x-signature", "")
    if settings.streams_secret:
        if not verify_webhook_signature(body, signature, settings.streams_secret):
            logger.warning("Invalid webhook signature from %s", request.client.host if request.client else "unknown")
            raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Moralis sends a verification request with empty body on stream creation
    if not payload or payload.get("txs") is None and payload.get("erc20Transfers") is None:
        logger.info("Moralis stream verification ping received")
        return {"status": "ok"}

    logger.info(
        "Stream event received: chainId=%s, block=%s, transfers=%d",
        payload.get("chainId", "?"),
        payload.get("block", {}).get("number", "?"),
        len(payload.get("erc20Transfers", [])),
    )

    # Parse events in background to return 200 quickly
    asyncio.create_task(_process_stream_payload(payload))

    return Response(content='{"status":"ok"}', media_type="application/json")


async def _process_stream_payload(payload: Dict) -> None:
    """Process stream payload: persist trades → update metrics → fire signals."""
    try:
        from analytics.scoring import update_wallet_score
        from analytics.wallet_analytics import compute_wallet_metrics
        from clients.moralis import get_moralis_client
        from ingestion.wallet_fetcher import _persist_trades, upsert_wallet
        from signals.detector import evaluate_trade_signals
        from sqlalchemy import select

        trades = parse_stream_event(payload)
        if not trades:
            return

        logger.info("Processing %d stream trades", len(trades))
        client = get_moralis_client()

        # Group trades by wallet
        by_wallet: Dict[str, List[Dict]] = {}
        for t in trades:
            key = f"{t['wallet_address']}_{t['chain']}"
            by_wallet.setdefault(key, []).append(t)

        for wallet_key, wallet_trades in by_wallet.items():
            addr, chain = wallet_trades[0]["wallet_address"], wallet_trades[0]["chain"]

            # Only process wallets we're tracking
            async with get_db() as db:
                result = await db.execute(
                    select(Wallet).where(
                        Wallet.address == addr, Wallet.chain == chain
                    )
                )
                wallet = result.scalar_one_or_none()

            if wallet is None:
                continue  # Skip untracked wallets from stream

            # Enrich with current price
            for t in wallet_trades:
                if t["price_usd"] == 0 and t["token_address"]:
                    try:
                        price_data = await client.get_token_price(
                            t["token_address"], t["chain"]
                        )
                        if price_data:
                            price = float(price_data.get("usdPrice", 0))
                            t["price_usd"] = price
                            t["amount_usd"] = price * t["token_amount"]
                    except Exception:
                        pass

            # Persist trades
            new_count = await _persist_trades(wallet, wallet_trades)
            if new_count > 0:
                await compute_wallet_metrics(wallet.id)
                await update_wallet_score(wallet.id)

                # Evaluate signals for each new trade
                async with get_db() as db:
                    from db.models import Trade
                    result = await db.execute(
                        select(Trade)
                        .where(Trade.wallet_id == wallet.id)
                        .order_by(Trade.timestamp.desc())
                        .limit(new_count)
                    )
                    recent_trades = result.scalars().all()

                async with get_db() as db:
                    w_result = await db.execute(
                        select(Wallet).where(Wallet.id == wallet.id)
                    )
                    fresh_wallet = w_result.scalar_one_or_none()

                if fresh_wallet:
                    for trade in recent_trades:
                        await evaluate_trade_signals(fresh_wallet, trade)

                # Deliver signals
                from alerts.telegram_alert import deliver_pending_signals
                await deliver_pending_signals()

    except Exception as e:
        logger.error("Stream payload processing failed: %s", e, exc_info=True)
