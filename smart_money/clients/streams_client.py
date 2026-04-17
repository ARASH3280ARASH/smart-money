from __future__ import annotations

"""
Moralis Streams API client.
Creates and manages EVM streams that push real-time wallet events
to the webhook endpoint via HTTP POST.

Docs: https://docs.moralis.io/streams-api/evm
"""

import hmac
import json
from typing import Any, Dict, List, Optional

import aiohttp

from config.settings import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)

STREAMS_BASE = "https://api.moralis-streams.com/streams/evm"


class StreamsClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "X-API-Key": self._settings.moralis_api_key,
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self, method: str, path: str, body: Optional[Dict] = None
    ) -> Optional[Dict]:
        session = await self._get_session()
        url = f"{STREAMS_BASE}{path}"
        try:
            async with session.request(
                method, url, json=body
            ) as resp:
                if resp.status in (200, 201):
                    return await resp.json(content_type=None)
                body_text = await resp.text()
                # 200 wrapped in {"result": ...} for list endpoints
                if resp.status == 200:
                    try:
                        return await resp.json(content_type=None)
                    except Exception:
                        pass
                logger.warning(
                    "Streams API %s %s → %d: %s",
                    method, path, resp.status, body_text[:300],
                )
                return None
        except aiohttp.ClientError as e:
            logger.error("Streams client error: %s", e)
            return None

    # ── Stream lifecycle ────────────────────────────────────────────────

    async def get_all_streams(self) -> List[Dict]:
        """List all existing streams for this API key."""
        result = await self._request("GET", "")
        if result:
            return result.get("result", [])
        return []

    async def create_stream(
        self,
        webhook_url: str,
        description: str = "Smart Money Analytics",
        tag: str = "smart_money",
        chains: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Create a new EVM stream. Returns stream_id on success.
        """
        payload = {
            "webhookUrl": webhook_url,
            "description": description,
            "tag": tag,
            "topic0": [],
            "allAddresses": False,
            "includeNativeTxs": True,
            "includeContractLogs": False,
            "includeInternalTxs": False,
            "includeAllTxLogs": False,
            "getNativeBalances": [],
            "chains": [f"0x{int(c, 16):x}" if c.startswith("0x") else _chain_hex(c)
                       for c in (chains or ["0x1"])],  # default: Ethereum mainnet
            "abi": [],
            "advancedOptions": [],
        }
        result = await self._request("PUT", "", body=payload)
        if result:
            stream_id = result.get("id")
            logger.info("Created Moralis stream: %s", stream_id)
            return stream_id
        return None

    async def add_addresses(
        self, stream_id: str, addresses: List[str]
    ) -> bool:
        """Add wallet addresses to an existing stream."""
        if not addresses:
            return True
        # Moralis accepts up to 100 addresses per call
        batch_size = 100
        for i in range(0, len(addresses), batch_size):
            batch = addresses[i : i + batch_size]
            result = await self._request(
                "POST",
                f"/{stream_id}/address",
                body={"address": batch},
            )
            if result is None:
                return False
        logger.info(
            "Added %d addresses to stream %s", len(addresses), stream_id
        )
        return True

    async def remove_addresses(
        self, stream_id: str, addresses: List[str]
    ) -> bool:
        """Remove addresses from a stream."""
        if not addresses:
            return True
        result = await self._request(
            "DELETE",
            f"/{stream_id}/address",
            body={"address": addresses},
        )
        return result is not None

    async def get_stream_addresses(self, stream_id: str) -> List[str]:
        """Get list of addresses currently on a stream."""
        result = await self._request("GET", f"/{stream_id}/address")
        if result:
            return [r.get("address", "") for r in result.get("result", [])]
        return []

    async def delete_stream(self, stream_id: str) -> bool:
        result = await self._request("DELETE", f"/{stream_id}")
        return result is not None

    async def update_stream(
        self, stream_id: str, update: Dict
    ) -> Optional[Dict]:
        return await self._request("PUT", f"/{stream_id}", body=update)

    # ── Smart sync ──────────────────────────────────────────────────────

    async def sync_wallet_addresses(
        self, stream_id: str, target_addresses: List[str]
    ) -> None:
        """
        Sync stream to exactly match target_addresses.
        Adds new, removes stale.
        """
        current = set(a.lower() for a in await self.get_stream_addresses(stream_id))
        target = set(a.lower() for a in target_addresses)

        to_add = list(target - current)
        to_remove = list(current - target)

        if to_add:
            await self.add_addresses(stream_id, to_add)
        if to_remove:
            await self.remove_addresses(stream_id, to_remove)

        if to_add or to_remove:
            logger.info(
                "Stream %s synced: +%d / -%d addresses",
                stream_id, len(to_add), len(to_remove),
            )

    async def ensure_stream(
        self,
        chains: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Return existing stream_id from settings, or create a new one.
        The webhook URL is built from WEBHOOK_BASE_URL setting.
        """
        settings = self._settings
        if settings.moralis_stream_id:
            logger.info("Using existing stream: %s", settings.moralis_stream_id)
            return settings.moralis_stream_id

        if not settings.webhook_base_url:
            logger.warning(
                "WEBHOOK_BASE_URL not set — skipping stream creation. "
                "Set it to your server's public IP (e.g. http://1.2.3.4:8000)"
            )
            return None

        webhook_url = f"{settings.webhook_base_url.rstrip('/')}/streams/webhook"

        # Check if stream already exists for this webhook
        existing = await self.get_all_streams()
        for s in existing:
            if s.get("webhookUrl") == webhook_url:
                stream_id = s["id"]
                logger.info("Found existing stream for this webhook: %s", stream_id)
                await _persist_stream_id(stream_id)
                return stream_id

        stream_id = await self.create_stream(
            webhook_url=webhook_url,
            chains=chains or [_chain_hex(c) for c in settings.chains],
        )
        if stream_id:
            await _persist_stream_id(stream_id)
        return stream_id


# ── Webhook signature verification ──────────────────────────────────────────


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    """
    Verify Moralis Streams webhook signature.
    Moralis computes: keccak256(body_bytes + secret_bytes)
    and sends it as the x-signature header.
    """
    if not secret or not signature:
        return False
    try:
        from Crypto.Hash import keccak as _keccak
        k = _keccak.new(digest_bits=256)
        k.update(body + secret.encode("utf-8"))
        expected = k.hexdigest()
        return hmac.compare_digest(signature.lower(), expected.lower())
    except Exception as e:
        logger.warning("Signature verification error: %s", e)
        return False


# ── Helpers ─────────────────────────────────────────────────────────────────


def _chain_hex(chain_id: str) -> str:
    """Convert chain name to hex chain ID for Moralis Streams."""
    mapping = {
        "eth": "0x1",
        "bsc": "0x38",
        "polygon": "0x89",
        "base": "0x2105",
        "arbitrum": "0xa4b1",
        "optimism": "0xa",
        "avalanche": "0xa86a",
    }
    return mapping.get(chain_id.lower(), "0x1")


async def _persist_stream_id(stream_id: str) -> None:
    """Write the stream_id to the .env file for persistence."""
    try:
        from pathlib import Path
        env_path = Path(__file__).resolve().parent.parent / ".env"
        content = env_path.read_text(encoding="utf-8")
        if "MORALIS_STREAM_ID=" in content:
            lines = content.splitlines()
            lines = [
                f"MORALIS_STREAM_ID={stream_id}"
                if l.startswith("MORALIS_STREAM_ID=")
                else l
                for l in lines
            ]
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            env_path.write_text(
                content + f"\nMORALIS_STREAM_ID={stream_id}\n", encoding="utf-8"
            )
        logger.info("Persisted stream ID %s to .env", stream_id)
    except Exception as e:
        logger.warning("Could not persist stream ID: %s", e)


# ── Stream event parser ──────────────────────────────────────────────────────


def parse_stream_event(payload: Dict) -> List[Dict]:
    """
    Convert a Moralis Streams webhook payload into a list of trade dicts
    compatible with ingestion/wallet_fetcher._persist_trades.

    Payload structure reference:
    https://docs.moralis.io/streams-api/evm/webhook-security
    """
    trades = []

    block = payload.get("block", {})
    block_number = block.get("number")
    block_timestamp = block.get("timestamp")
    chain_id = payload.get("chainId", "0x1")
    chain = _hex_to_chain_name(chain_id)

    erc20_transfers = payload.get("erc20Transfers", [])
    confirmed = payload.get("confirmed", True)

    if not confirmed:
        return []  # Skip unconfirmed blocks

    for transfer in erc20_transfers:
        from_addr = (transfer.get("from") or "").lower()
        to_addr = (transfer.get("to") or "").lower()
        token_addr = (transfer.get("contract") or "").lower()
        symbol = transfer.get("tokenSymbol", "")
        value_formatted = transfer.get("valueWithDecimals") or transfer.get("value", "0")
        tx_hash = transfer.get("transactionHash", "")

        try:
            token_amount = float(value_formatted)
        except (TypeError, ValueError):
            token_amount = 0.0

        # We create two records: one for sender (sell) and one for receiver (buy)
        ts_str = None
        if block_timestamp:
            from datetime import datetime, timezone
            try:
                ts_str = datetime.fromtimestamp(
                    int(block_timestamp), tz=timezone.utc
                ).replace(tzinfo=None)
            except Exception:
                ts_str = None

        if from_addr and token_addr:
            trades.append({
                "wallet_address": from_addr,
                "chain": chain,
                "tx_hash": tx_hash,
                "token_address": token_addr,
                "token_symbol": symbol,
                "trade_type": "sell",
                "amount_usd": 0.0,  # will be enriched
                "token_amount": token_amount,
                "price_usd": 0.0,
                "block_number": int(block_number) if block_number else None,
                "timestamp": ts_str,
            })

        if to_addr and token_addr:
            trades.append({
                "wallet_address": to_addr,
                "chain": chain,
                "tx_hash": tx_hash,
                "token_address": token_addr,
                "token_symbol": symbol,
                "trade_type": "buy",
                "amount_usd": 0.0,
                "token_amount": token_amount,
                "price_usd": 0.0,
                "block_number": int(block_number) if block_number else None,
                "timestamp": ts_str,
            })

    return trades


def _hex_to_chain_name(hex_id: str) -> str:
    mapping = {
        "0x1": "eth",
        "0x38": "bsc",
        "0x89": "polygon",
        "0x2105": "base",
        "0xa4b1": "arbitrum",
        "0xa": "optimism",
    }
    return mapping.get(hex_id.lower(), "eth")


# Singleton
_client: StreamsClient | None = None


def get_streams_client() -> StreamsClient:
    global _client
    if _client is None:
        _client = StreamsClient()
    return _client
