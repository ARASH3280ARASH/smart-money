from __future__ import annotations

"""
Async Moralis API client.
- CU-aware rate limiting
- TTL response caching
- Exponential backoff on 429 / 5xx
- API usage logging to DB (fire-and-forget)
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

import aiohttp
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import get_settings
from utils.cache import (
    AsyncTTLCache,
    get_metrics_cache,
    get_price_cache,
    get_token_cache,
    get_wallet_cache,
)
from utils.logger import get_logger
from utils.rate_limiter import get_rate_limiter

logger = get_logger(__name__)

MORALIS_BASE = "https://deep-index.moralis.io/api/v2.2"
SOLANA_BASE = "https://solana-gateway.moralis.io"

# CU costs per endpoint (approximate – adjust based on /info/endpointWeights)
CU_COSTS: Dict[str, int] = {
    "wallet_history": 5,
    "wallet_tokens": 5,
    "wallet_net_worth": 3,
    "wallet_pnl_summary": 10,
    "wallet_pnl_breakdown": 10,
    "token_price": 2,
    "token_transfers": 5,
    "token_top_traders": 10,
    "defi_positions": 5,
    "discovery_wallets": 10,
    "discovery_tokens": 10,
    "pairs_search": 5,
    "default": 2,
}


class MoralisError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class MoralisClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._rate_limiter = get_rate_limiter()
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "X-API-Key": self._settings.moralis_api_key,
                    "Accept": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        endpoint_key: str = "default",
    ) -> Any:
        cu = CU_COSTS.get(endpoint_key, CU_COSTS["default"])
        await self._rate_limiter.acquire(cu)

        session = await self._get_session()
        attempt = 0
        backoff = 1.0

        while attempt < 6:
            attempt += 1
            try:
                async with session.request(
                    method, url, params=params
                ) as resp:
                    await self._log_usage(url, cu, resp.status)
                    if resp.status == 200:
                        return await resp.json(content_type=None)
                    if resp.status == 429:
                        retry_after = float(
                            resp.headers.get("Retry-After", backoff)
                        )
                        logger.warning("429 rate limit on %s, waiting %.1fs", url, retry_after)
                        await asyncio.sleep(retry_after)
                        backoff = min(backoff * 2, 30)
                        continue
                    if resp.status >= 500:
                        logger.warning("5xx on %s (attempt %d)", url, attempt)
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 30)
                        continue
                    if resp.status == 404:
                        return None
                    body = await resp.text()
                    raise MoralisError(resp.status, body[:200])
            except aiohttp.ClientError as exc:
                logger.warning("Network error on %s: %s (attempt %d)", url, exc, attempt)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

        logger.error("Exhausted retries for %s", url)
        return None

    async def _log_usage(self, url: str, cu: int, status: int) -> None:
        """Fire-and-forget API usage persistence."""
        try:
            from db.session import get_db
            from db.models import ApiUsage
            from datetime import datetime

            async with get_db() as db:
                entry = ApiUsage(
                    endpoint=url.replace(MORALIS_BASE, "")[:128],
                    cu_cost=cu,
                    status_code=status,
                    called_at=datetime.utcnow(),
                )
                db.add(entry)
        except Exception:
            pass  # Never fail a request due to logging

    # ── EVM Wallet endpoints ────────────────────────────────────────────

    async def get_wallet_history(
        self,
        address: str,
        chain: str,
        limit: int = 100,
        from_date: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> Optional[Dict]:
        cache = get_wallet_cache()
        key = f"wh:{chain}:{address}:{limit}:{from_date}:{cursor}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        params: Dict[str, Any] = {"chain": chain, "limit": limit, "include_internal_transactions": "false"}
        if from_date:
            params["from_date"] = from_date
        if cursor:
            params["cursor"] = cursor

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/wallets/{address}/history",
            params=params,
            endpoint_key="wallet_history",
        )
        if result is not None:
            await cache.set(key, result)
        return result

    async def get_wallet_token_balances(
        self, address: str, chain: str
    ) -> Optional[List[Dict]]:
        cache = get_wallet_cache()
        key = f"wtb:{chain}:{address}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/wallets/{address}/tokens",
            params={"chain": chain},
            endpoint_key="wallet_tokens",
        )
        data = result.get("result") if isinstance(result, dict) else result
        if data is not None:
            await cache.set(key, data)
        return data

    async def get_wallet_net_worth(
        self, address: str, chains: Optional[List[str]] = None
    ) -> Optional[Dict]:
        cache = get_metrics_cache()
        chain_str = ",".join(chains or ["eth"])
        key = f"wnw:{address}:{chain_str}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        params: Dict[str, Any] = {}
        if chains:
            params["chains"] = chains

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/wallets/{address}/net-worth",
            params=params,
            endpoint_key="wallet_net_worth",
        )
        if result is not None:
            await cache.set(key, result)
        return result

    async def get_wallet_pnl_summary(
        self, address: str, chain: str
    ) -> Optional[Dict]:
        cache = get_metrics_cache()
        key = f"wpnl:{chain}:{address}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/wallets/{address}/profitability/summary",
            params={"chain": chain},
            endpoint_key="wallet_pnl_summary",
        )
        if result is not None:
            await cache.set(key, result)
        return result

    async def get_wallet_pnl_breakdown(
        self, address: str, chain: str
    ) -> Optional[Dict]:
        cache = get_metrics_cache()
        key = f"wpnlb:{chain}:{address}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/wallets/{address}/profitability",
            params={"chain": chain},
            endpoint_key="wallet_pnl_breakdown",
        )
        if result is not None:
            await cache.set(key, result)
        return result

    # ── Token endpoints ─────────────────────────────────────────────────

    async def get_token_price(self, address: str, chain: str) -> Optional[Dict]:
        cache = get_price_cache()
        key = f"tp:{chain}:{address}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/erc20/{address}/price",
            params={"chain": chain},
            endpoint_key="token_price",
        )
        if result is not None:
            await cache.set(key, result)
        return result

    async def get_token_transfers(
        self,
        address: str,
        chain: str,
        limit: int = 100,
        from_date: Optional[str] = None,
    ) -> Optional[Dict]:
        cache = get_token_cache()
        key = f"tt:{chain}:{address}:{limit}:{from_date}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        params: Dict[str, Any] = {"chain": chain, "limit": limit}
        if from_date:
            params["from_date"] = from_date

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/erc20/{address}/transfers",
            params=params,
            endpoint_key="token_transfers",
        )
        if result is not None:
            await cache.set(key, result)
        return result

    async def get_token_top_traders(
        self, address: str, chain: str, days: int = 30
    ) -> Optional[Dict]:
        cache = get_token_cache()
        key = f"ttt:{chain}:{address}:{days}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/erc20/{address}/top-gainers",
            params={"chain": chain, "days": days},
            endpoint_key="token_top_traders",
        )
        if result is not None:
            await cache.set(key, result)
        return result

    async def get_token_metadata(self, address: str, chain: str) -> Optional[Dict]:
        cache = get_token_cache()
        key = f"tm:{chain}:{address}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/erc20/metadata",
            params={"chain": chain, "addresses[]": address},
            endpoint_key="default",
        )
        data = result[0] if isinstance(result, list) and result else result
        if data is not None:
            await cache.set(key, data)
        return data

    # ── Discovery endpoints ─────────────────────────────────────────────

    async def get_trending_tokens(
        self, chain: str = "eth", limit: int = 20
    ) -> Optional[List[Dict]]:
        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/discovery/tokens",
            params={"chain": chain, "limit": limit, "sort_by": "volume_change_usd"},
            endpoint_key="discovery_tokens",
        )
        return result.get("result") if isinstance(result, dict) else result

    async def get_discovery_wallets(
        self, chain: str = "eth", limit: int = 20
    ) -> Optional[List[Dict]]:
        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/discovery/wallets",
            params={"chain": chain, "limit": limit},
            endpoint_key="discovery_wallets",
        )
        return result.get("result") if isinstance(result, dict) else result

    async def get_pairs_for_token(
        self, address: str, chain: str
    ) -> Optional[Dict]:
        cache = get_token_cache()
        key = f"pairs:{chain}:{address}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/erc20/{address}/pairs",
            params={"chain": chain},
            endpoint_key="pairs_search",
        )
        if result is not None:
            await cache.set(key, result)
        return result

    async def get_defi_positions(
        self, address: str, chain: str
    ) -> Optional[Dict]:
        cache = get_metrics_cache()
        key = f"defi:{chain}:{address}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        result = await self._request(
            "GET",
            f"{MORALIS_BASE}/wallets/{address}/defi/positions",
            params={"chain": chain},
            endpoint_key="defi_positions",
        )
        if result is not None:
            await cache.set(key, result)
        return result

    # ── Solana endpoints ────────────────────────────────────────────────

    async def get_solana_wallet_history(
        self, address: str, limit: int = 100
    ) -> Optional[List[Dict]]:
        cache = get_wallet_cache()
        key = f"sol_wh:{address}:{limit}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        result = await self._request(
            "GET",
            f"{SOLANA_BASE}/account/mainnet/{address}/swaps",
            params={"limit": limit},
            endpoint_key="wallet_history",
        )
        if result is not None:
            await cache.set(key, result)
        return result

    async def get_solana_token_price(self, address: str) -> Optional[Dict]:
        cache = get_price_cache()
        key = f"sol_tp:{address}"
        cached = await cache.get(key)
        if cached is not None:
            return cached

        result = await self._request(
            "GET",
            f"{SOLANA_BASE}/token/mainnet/{address}/price",
            endpoint_key="token_price",
        )
        if result is not None:
            await cache.set(key, result)
        return result


# Singleton
_client: MoralisClient | None = None


def get_moralis_client() -> MoralisClient:
    global _client
    if _client is None:
        _client = MoralisClient()
    return _client
