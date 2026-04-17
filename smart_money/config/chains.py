from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class ChainConfig:
    chain_id: str          # Moralis chain id string (e.g. "eth")
    name: str              # Human-readable name
    native_symbol: str     # e.g. ETH, BNB
    block_time_sec: float  # Approximate block time
    explorer_url: str      # Block explorer base URL
    moralis_chain: str     # Chain identifier used by Moralis API
    solana: bool = False   # True for Solana (uses different API path)


CHAINS: Dict[str, ChainConfig] = {
    "eth": ChainConfig(
        chain_id="eth",
        name="Ethereum",
        native_symbol="ETH",
        block_time_sec=12.0,
        explorer_url="https://etherscan.io",
        moralis_chain="eth",
    ),
    "bsc": ChainConfig(
        chain_id="bsc",
        name="BNB Smart Chain",
        native_symbol="BNB",
        block_time_sec=3.0,
        explorer_url="https://bscscan.com",
        moralis_chain="bsc",
    ),
    "polygon": ChainConfig(
        chain_id="polygon",
        name="Polygon",
        native_symbol="MATIC",
        block_time_sec=2.0,
        explorer_url="https://polygonscan.com",
        moralis_chain="polygon",
    ),
    "base": ChainConfig(
        chain_id="base",
        name="Base",
        native_symbol="ETH",
        block_time_sec=2.0,
        explorer_url="https://basescan.org",
        moralis_chain="base",
    ),
    "arbitrum": ChainConfig(
        chain_id="arbitrum",
        name="Arbitrum One",
        native_symbol="ETH",
        block_time_sec=0.25,
        explorer_url="https://arbiscan.io",
        moralis_chain="arbitrum",
    ),
    "optimism": ChainConfig(
        chain_id="optimism",
        name="Optimism",
        native_symbol="ETH",
        block_time_sec=2.0,
        explorer_url="https://optimistic.etherscan.io",
        moralis_chain="optimism",
    ),
    "solana": ChainConfig(
        chain_id="solana",
        name="Solana",
        native_symbol="SOL",
        block_time_sec=0.4,
        explorer_url="https://solscan.io",
        moralis_chain="mainnet",
        solana=True,
    ),
}


def get_chain(chain_id: str) -> Optional[ChainConfig]:
    return CHAINS.get(chain_id.lower())


def get_explorer_tx_url(chain_id: str, tx_hash: str) -> str:
    cfg = get_chain(chain_id)
    if cfg is None:
        return tx_hash
    if cfg.solana:
        return f"{cfg.explorer_url}/tx/{tx_hash}"
    return f"{cfg.explorer_url}/tx/{tx_hash}"


def get_explorer_address_url(chain_id: str, address: str) -> str:
    cfg = get_chain(chain_id)
    if cfg is None:
        return address
    if cfg.solana:
        return f"{cfg.explorer_url}/account/{address}"
    return f"{cfg.explorer_url}/address/{address}"
