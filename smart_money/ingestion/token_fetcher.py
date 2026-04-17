from __future__ import annotations

"""
Fetches token data, prices, transfers, top traders, and new liquidity events.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from clients.moralis import get_moralis_client
from config.settings import get_settings
from db.models import Token, TokenEvent
from db.session import get_db
from utils.logger import get_logger

logger = get_logger(__name__)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


async def fetch_token_metadata(address: str, chain: str) -> Optional[Dict]:
    client = get_moralis_client()
    meta = await client.get_token_metadata(address, chain)
    if not meta:
        return None

    async with get_db() as db:
        result = await db.execute(
            select(Token).where(Token.address == address.lower(), Token.chain == chain)
        )
        token = result.scalar_one_or_none()
        if token is None:
            token = Token(address=address.lower(), chain=chain)
            db.add(token)

        token.symbol = meta.get("symbol", token.symbol or "")
        token.name = meta.get("name", token.name or "")
        token.decimals = int(meta.get("decimals", 18) or 18)

    return meta


async def fetch_token_price(address: str, chain: str) -> float:
    """Returns USD price of token. Updates DB record."""
    client = get_moralis_client()
    data = await client.get_token_price(address, chain)
    if not data:
        return 0.0

    price = _safe_float(data.get("usdPrice"))

    async with get_db() as db:
        result = await db.execute(
            select(Token).where(Token.address == address.lower(), Token.chain == chain)
        )
        token = result.scalar_one_or_none()
        if token:
            token.last_price_usd = price
            token.price_updated_at = datetime.utcnow()

    return price


async def fetch_trending_tokens(chain: str) -> List[Dict]:
    """Fetch trending tokens on chain for discovery."""
    client = get_moralis_client()
    tokens = await client.get_trending_tokens(chain, limit=20)
    return tokens or []


async def fetch_top_traders_for_token(
    token_address: str, chain: str, days: int = 30
) -> List[Dict]:
    """Fetch top profitable traders for a specific token."""
    client = get_moralis_client()
    data = await client.get_token_top_traders(token_address, chain, days=days)
    if not data:
        return []
    return data.get("result", data) if isinstance(data, dict) else data


async def fetch_token_recent_transfers(
    token_address: str, chain: str, hours: int = 24
) -> List[Dict]:
    """Fetch recent transfers for a token to detect coordinated activity."""
    client = get_moralis_client()
    from_date = (datetime.utcnow() - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    data = await client.get_token_transfers(
        token_address, chain, limit=200, from_date=from_date
    )
    if not data:
        return []
    return data.get("result", []) if isinstance(data, dict) else []


async def fetch_token_pairs(token_address: str, chain: str) -> List[Dict]:
    """Fetch DEX pairs for liquidity analysis."""
    client = get_moralis_client()
    data = await client.get_pairs_for_token(token_address, chain)
    if not data:
        return []
    return data.get("pairs", data.get("result", [])) if isinstance(data, dict) else []


async def detect_new_liquidity(
    token_address: str, chain: str, min_usd: float = 50_000.0
) -> Optional[Dict]:
    """
    Check if a token has new significant liquidity.
    Returns event dict if detected, else None.
    """
    pairs = await fetch_token_pairs(token_address, chain)
    if not pairs:
        return None

    total_liquidity = sum(
        _safe_float(p.get("liquidity_usd") or p.get("usdLiquidity")) for p in pairs
    )
    if total_liquidity < min_usd:
        return None

    # Check for recently created pairs
    now = datetime.utcnow()
    new_pairs = []
    for pair in pairs:
        created_raw = pair.get("created_at") or pair.get("pairCreatedAt")
        if created_raw:
            try:
                created = datetime.fromisoformat(
                    str(created_raw).replace("Z", "+00:00")
                ).replace(tzinfo=None)
                if (now - created).total_seconds() < 86400:  # last 24h
                    new_pairs.append(pair)
            except Exception:
                pass

    if not new_pairs:
        return None

    return {
        "token_address": token_address,
        "chain": chain,
        "event_type": "NEW_LIQUIDITY",
        "total_liquidity_usd": total_liquidity,
        "new_pair_count": len(new_pairs),
        "pairs": new_pairs[:3],
    }


async def store_token_event(
    token_address: str,
    chain: str,
    event_type: str,
    wallets: List[str],
    score: float,
    metadata: Dict,
) -> TokenEvent:
    import json as _json

    async with get_db() as db:
        # Resolve token FK
        result = await db.execute(
            select(Token).where(
                Token.address == token_address.lower(), Token.chain == chain
            )
        )
        token = result.scalar_one_or_none()
        token_id = token.id if token else None

        event = TokenEvent(
            token_id=token_id,
            token_address=token_address.lower(),
            chain=chain,
            event_type=event_type,
            wallets_json=_json.dumps(wallets),
            score=score,
            metadata_json=_json.dumps(metadata),
            timestamp=datetime.utcnow(),
        )
        db.add(event)
        await db.flush()
        return event


async def discover_wallets_from_token(
    token_address: str, chain: str
) -> List[Tuple[str, str]]:
    """
    Return top traders for a token as (address, chain) tuples for seeding.
    """
    traders = await fetch_top_traders_for_token(token_address, chain)
    results = []
    for t in traders[:20]:
        addr = t.get("address") or t.get("wallet_address")
        if addr:
            results.append((addr.lower(), chain))
    return results
